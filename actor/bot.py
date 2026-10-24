"""常駐ボット（ルームを監視して、スキルを適用した返事を書き込む）。

「自分だけの常駐ボット」の最小構成。ボットは **Actor の完全なインスタンス**として
ローカルで動き、ルーム（共有ログ）を経由して人間とやり取りする。

    # 端末1: サーバー（ルームを外から見えるようにする）
    python3 -m actor serve --port 8080

    # 端末2: 常駐ボット（mika スキルを固定適用）
    python3 -m actor bot --room actor-x --skills mika

    # 端末3: 話しかける
    curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
      -H 'Content-Type: application/json' -d '{"text":"おはよー"}'
    curl -s localhost:8080/api/rooms/actor-x?after=0 | jq '.messages[].text'

ボットは SKILL.md をターンごとに読み直すので、**動かしたままスキルを編集**すると
次の返事から反映される（persona の試行錯誤がしやすい）。
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .chat import ChatEngine
from .config import Settings
from .providers import ProviderError
from .rooms import DEFAULT_ROOM, RoomStore, Room
from .skills import slugify

__all__ = ["RoomBot", "run_bot"]


# ----------------------------------------------------------------------
# HTTP クライアント（標準ライブラリのみ）
# ----------------------------------------------------------------------
class RoomClient:
    """ルーム API の薄いクライアント。"""

    def __init__(self, base_url: str, timeout: float = 30.0, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Actor-Token"] = self.token
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:  # pragma: no cover
                pass
            raise RuntimeError(f"{method} {path} → {exc.code} {detail[:200]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{method} {path} → 接続できません: {exc.reason}") from exc
        return json.loads(body) if body else {}

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def ensure_room(self, name: str, skills: Sequence[str] = ()) -> dict[str, Any]:
        return self._request("POST", "/api/rooms", {"name": name, "skills": list(skills)})

    def messages(self, name: str, after: int = 0, attachments: bool = False) -> dict[str, Any]:
        query = {"after": after}
        if attachments:  # 添付は base64 なので、必要なときだけ取り寄せる
            query["attachments"] = 1
        return self._request(
            "GET", f"/api/rooms/{urllib.parse.quote(name)}?{urllib.parse.urlencode(query)}"
        )

    def post(self, name: str, text: str, *, role: str = "user", author: str = "",
             skills: Sequence[str] = (), model: str = "", simulated: bool = False,
             usage: dict[str, Any] | None = None, reply: bool = False,
             tools: Sequence[str] = (), attachments: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
        return self._request("POST", f"/api/rooms/{urllib.parse.quote(name)}/messages", {
            "text": text,
            "role": role,
            "author": author,
            "skills": list(skills),
            "model": model,
            "simulated": simulated,
            "usage": usage or {},
            "tools": list(tools),
            "attachments": list(attachments),
            "reply": reply,
        })


# ----------------------------------------------------------------------
# ボット
# ----------------------------------------------------------------------
@dataclass
class BotState:
    cursor: int = 0
    session_id: str = ""


@dataclass
class RoomBot:
    """ルームを監視して返事を書き込む常駐プロセス。"""

    settings: Settings
    room: str = DEFAULT_ROOM
    api_base: str = "http://127.0.0.1:8080"
    skills: list[str] = field(default_factory=list)
    auto: bool | None = None
    interval: float = 1.5
    bot_name: str = "Actor"
    stream: bool = False
    once: bool = False
    from_now: bool = False  # 起動時点より前の履歴には反応しない
    respond_to: str = "user"  # user | all
    mention: str = ""  # 指定すると、その文字列を含む発言にだけ反応する

    # 実行時
    engine: ChatEngine | None = None
    client: RoomClient | None = None
    state: BotState = field(default_factory=BotState)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    # ------------------------------------------------------------------
    def setup(self) -> None:
        self.engine = ChatEngine(self.settings)
        self.client = RoomClient(
            self.api_base, timeout=max(15.0, self.settings.timeout), token=self.settings.room_token
        )
        self.state = self._load_state()
        if self.from_now and not self.state.cursor:
            try:
                room = self.client.messages(self.room, after=0)
                self.state.cursor = int(room.get("cursor") or 0)
                if self.state.cursor:
                    self.log(f"--from-now: 既存の {self.state.cursor} 件は読み飛ばします")
            except RuntimeError:
                pass
        missing = [name for name in self.skills if self.engine.library.get(slugify(name)) is None]
        if missing:
            self.log(f"警告: スキルが見つかりません: {', '.join(missing)}")
        try:
            health = self.client.health()
            self.log(
                f"接続OK: {self.api_base} (provider={health.get('provider')}, skills={health.get('skills')})"
            )
        except RuntimeError as exc:
            self.log(f"警告: サーバーに接続できません → {exc}")
        self.client.ensure_room(self.room, self.skills)

    # ---- 状態（カーソル）の永続化 ----
    @property
    def state_path(self) -> Path:
        return self.settings.data_dir / f"bot-{self.room}.json"

    def _load_state(self) -> BotState:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return BotState()
        return BotState(cursor=int(data.get("cursor") or 0), session_id=str(data.get("session_id") or ""))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"cursor": self.state.cursor, "session_id": self.state.session_id}, indent=2),
            encoding="utf-8",
        )

    # ---- ログ ----
    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] [bot:{self.room}] {message}", flush=True)

    # ---- セッション ----
    def session(self):
        assert self.engine is not None
        store = self.engine.store
        if self.state.session_id:
            session = store.get(self.state.session_id)
            if session is not None:
                return session
        room: Room | None = None
        if self.client is not None:
            try:
                room = Room.from_dict(self.client.messages(self.room, after=0))
            except RuntimeError:
                room = None
        session = store.create(
            title=f"room:{self.room}",
            skills=list(self.skills) or (room.skills if room else []),
            model=self.settings.model,
        )
        self.state.session_id = session.id
        self._save_state()
        return session

    # ------------------------------------------------------------------
    # 本体
    # ------------------------------------------------------------------
    def run(self) -> int:
        if self.engine is None or self.client is None:
            self.setup()
        assert self.engine is not None and self.client is not None

        self._install_signal_handlers()
        pinned = ", ".join(self.skills) or "（自動選択）"
        self.log(
            f"起動: room={self.room} / skills={pinned} / interval={self.interval}s / "
            f"cursor={self.state.cursor} / model={self.settings.model}"
            + ("  [シミュレータ]" if self.engine.provider.is_mock else "")
        )
        print(f"[bot:{self.room}] 待ち受け中…（Ctrl-C で停止） 話しかけるには:", flush=True)
        print(
            f"  curl -s -X POST {self.api_base}/api/rooms/{self.room}/messages "
            f"-H 'Content-Type: application/json' -d '{{\"text\":\"おはよう\"}}'",
            flush=True,
        )

        errors = 0
        while not self._stop.is_set():
            try:
                handled = self.tick()
                errors = 0
            except RuntimeError as exc:
                errors += 1
                if errors <= 3 or errors % 20 == 0:
                    self.log(f"通信エラー（リトライします）: {exc}")
                handled = 0
            except ProviderError as exc:
                self.log(f"モデルエラー: {exc}")
                handled = 0
            if self.once and handled >= 0:
                return 0
            if not handled:
                self._wait(self.interval)
        self.log("停止しました。")
        return 0

    def tick(self) -> int:
        """1回ポーリングして、必要なら返事をする。処理した発言数を返す。"""
        assert self.client is not None
        try:
            payload = self.client.messages(self.room, after=self.state.cursor, attachments=True)
        except RuntimeError as exc:
            # ルームが消えていたら作り直す（保存先を消した・別データで再起動した等）
            if " 404 " in str(exc) or "404 " in str(exc):
                self.log("ルームが見つからないので作り直します")
                self.client.ensure_room(self.room, self.skills)
                self.state.cursor = 0
                self._save_state()
                return 0
            raise
        messages = payload.get("messages", [])
        handled = 0
        for message in messages:
            self.state.cursor = max(self.state.cursor, int(message.get("seq") or 0))
            if not self._should_respond(message):
                continue
            self.respond(message)
            handled += 1
        if handled:
            self._save_state()
        elif messages:
            self._save_state()
        return handled

    def _should_respond(self, message: dict[str, Any]) -> bool:
        if message.get("role") != "user":
            return False
        author = str(message.get("author") or "")
        if author and author == self.bot_name:
            return False  # 自分の発言には反応しない
        text = str(message.get("text") or "")
        if not text.strip():
            return False
        if self.mention and self.mention not in text:
            return False
        return True

    def respond(self, message: dict[str, Any]) -> None:
        assert self.client is not None and self.engine is not None
        text = str(message.get("text") or "")
        author = str(message.get("author") or "user")
        self.log(f"← {author}: {' '.join(text.split())[:80]}")
        session = self.session()
        explicit = [s for s in (message.get("skills") or []) if s] or None
        answer = ""
        skills: list[str] = []
        model = ""
        simulated = False
        usage: dict[str, Any] = {}
        thoughts = ""
        tools: list[str] = []
        try:
            for event in self.engine.turn(
                session, text,
                explicit_skills=explicit or (list(self.skills) or None),
                auto=self.auto,
                stream=self.stream,
                attachments=list(message.get("attachments") or []),
            ):
                kind = event.get("type")
                if kind == "selection":
                    skills = event.get("names", [])
                elif kind == "text":
                    answer += event.get("text", "")
                    if self.stream:
                        sys.stdout.write(event["text"])
                        sys.stdout.flush()
                elif kind == "thought":
                    thoughts += event.get("text", "")
                elif kind == "tool_call":
                    self.log(f"  🛠 {event.get('name')} {event.get('args') or ''}")
                    name = str(event.get("name") or "")
                    if name and name not in tools:
                        tools.append(name)
                elif kind == "done":
                    usage = event.get("usage", {})
                    model = event.get("model", "")
                    simulated = bool(event.get("simulated"))
                    # done.tool_calls は {"name","args"} のリスト（ログ用に名前だけ取り出す）
                    tools = [c.get("name", "") if isinstance(c, dict) else str(c)
                             for c in (event.get("tool_calls") or [])] or tools
        except ProviderError as exc:
            self.log(f"生成に失敗しました: {exc}")
            self.client.post(
                self.room, f"（エラー: {exc}）", role="assistant", author=self.bot_name,
                skills=["error"],
            )
            return
        if self.stream:
            print(flush=True)
        if not answer.strip():
            answer = "（空の応答が返りました。もう一度話しかけてみてください）"
        self.client.post(
            self.room, answer, role="assistant", author=self.bot_name,
            skills=skills, model=model, simulated=simulated, usage=usage, tools=tools,
        )
        if thoughts.strip():
            self.log(f"思考ログ {len(thoughts)} 文字（セッション {session.id} に保存）")
        self.log(
            f"→ {self.bot_name}: {' '.join(answer.split())[:80]}"
            f"  [skills={', '.join(skills) or '-'} tokens={usage.get('totalTokenCount', 0)}"
            f"{' tools=' + ','.join(tools) if tools else ''}]"
        )

    # ---- 停止処理 ----
    def _install_signal_handlers(self) -> None:
        def handler(signum: int, frame: Any) -> None:  # pragma: no cover - シグナル
            self.log("停止要求を受け取りました…")
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # メインスレッド以外
                pass

    def _wait(self, seconds: float) -> None:
        self._stop.wait(max(0.05, seconds))

    def stop(self) -> None:
        self._stop.set()


# ----------------------------------------------------------------------
# 便利関数
# ----------------------------------------------------------------------
def run_bot(
    settings: Settings,
    room: str = DEFAULT_ROOM,
    *,
    api_base: str = "http://127.0.0.1:8080",
    skills: Sequence[str] | None = None,
    auto: bool | None = None,
    interval: float = 1.5,
    bot_name: str = "Actor",
    stream: bool = False,
    once: bool = False,
    from_now: bool = False,
    mention: str = "",
    on_ready: Callable[[RoomBot], None] | None = None,
) -> int:
    bot = RoomBot(
        settings=settings,
        room=room,
        api_base=api_base,
        skills=[s for s in (skills or [])],
        auto=auto,
        interval=interval,
        bot_name=bot_name,
        stream=stream,
        once=once,
        from_now=from_now,
        mention=mention,
    )
    bot.setup()
    if on_ready is not None:
        on_ready(bot)
    return bot.run()


def summarize_room(store: RoomStore, name: str) -> list[str]:
    """ルームの中身を表示用（CLI の rooms コマンド）に整形する。"""
    room = store.get(name)
    if room is None:
        return []
    lines = []
    for message in room.messages:
        who = message.author or message.role
        lines.append(f"[{message.seq:>4}] {who:<10} {message.text.splitlines()[0][:100] if message.text else ''}")
    return lines
