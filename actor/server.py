"""Web UI と HTTP API を提供するサーバー（標準ライブラリのみ）。

* ``/``             … 同梱の Web UI（HTML/CSS/JS）
* ``/api/*``        … JSON API
* ``/api/chat/stream`` … Server-Sent Events でトークンを逐次配信

外部フレームワークを使わないため、``pip install`` 不要でそのまま起動できる。
"""

from __future__ import annotations

import json
import ipaddress
import mimetypes
import posixpath
import secrets
import traceback
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .chat import ChatEngine
from .config import (
    KNOWN_MODELS,
    Settings,
    KeyEntry,
    clear_api_key,
    mask_key,
    write_api_key,
)
from .providers import ProviderError, get_provider
from .rooms import DEFAULT_ROOM, Room, RoomStore
from .webhooks import WebhookDispatcher
from .skills import slugify

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 64 * 1024 * 1024  # 画像/PDF の base64 を載せられるように広めに取る

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def _int_param(query: dict[str, list[str]], key: str, default: int = 0) -> int:
    raw = (query.get(key) or [""])[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


class ActorHandler(BaseHTTPRequestHandler):
    server_version = f"Actor/{__version__}"
    protocol_version = "HTTP/1.1"

    # ---- インフラ ----
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if getattr(self.server, "quiet", False):
            return
        message = fmt % args
        # 頻繁に叩かれるものはログから外す（ヘルスチェック・ルームのポーリング）
        if "/api/health" in message:
            return
        if message.startswith('"GET /api/rooms/') and "after=" in message:
            return
        print(f"[actor] {self.address_string()} {message}")

    def handle_one_request(self) -> None:  # 接続断を静かに処理
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    # ---- ヘルパ ----
    @property
    def engine(self) -> ChatEngine:
        return self.server.engine  # type: ignore[attr-defined]

    @property
    def rooms(self) -> RoomStore:
        store = getattr(self.server, "rooms", None)
        if store is None:
            store = RoomStore(self.settings.data_dir / "rooms")
            self.server.rooms = store  # type: ignore[attr-defined]
        return store

    @property
    def settings(self) -> Settings:
        return self.engine.settings

    @property
    def webhooks(self) -> WebhookDispatcher:
        dispatcher = getattr(self.server, "webhooks", None)
        if dispatcher is None:
            quiet = bool(getattr(self.server, "quiet", False))
            dispatcher = WebhookDispatcher(
                url=self.settings.webhook_url,
                secret=self.settings.webhook_secret,
                events=self.settings.webhook_events,
                timeout=self.settings.webhook_timeout,
                on_log=None if quiet else (lambda msg: print(f"[actor] webhook: {msg}")),
            )
            self.server.webhooks = dispatcher  # type: ignore[attr-defined]
        return dispatcher

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if urllib.parse.urlparse(self.path).path.startswith("/api/"):
            self._cors()
        # プレビュー（iframe 埋め込み）でも動くようにフレーム制限を掛けない
        self.send_header("Content-Security-Policy", "frame-ancestors *")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, **extra: Any) -> None:
        self._json({"error": message, "status": status, **extra}, status=status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("リクエストが大きすぎます")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"JSON を解釈できません: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON オブジェクトを送ってください")
        return payload

    # ---- ルーティング ----
    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._error(400, "不正な Host ヘッダーです")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Allow", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")

    # ---- ルームのトークン認証 ----
    def _token_from_request(self) -> str:
        """``X-Actor-Token`` / ``Authorization: Bearer`` / ``?token=`` の順に探す。"""
        for header in ("X-Actor-Token", "X-Room-Token"):
            value = (self.headers.get(header) or "").strip()
            if value:
                return value
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (query.get("token") or [""])[0].strip()

    def _token_ok(self) -> bool:
        expected = self.settings.room_token
        if not expected:
            return True
        provided = self._token_from_request()
        return bool(provided) and secrets.compare_digest(provided, expected)

    def _require_token(self) -> None:
        if not self._token_ok():
            raise PermissionError(
                "ルームのトークンが必要です"
                "（X-Actor-Token ヘッダ / Authorization: Bearer / ?token= のいずれか）"
            )

    def _cors(self) -> None:
        origin = (self.headers.get("Origin") or "").strip()
        allowed = set(self.settings.cors_origins)
        if origin and origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Actor-Token, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

    def _valid_host(self) -> bool:
        value = (self.headers.get("Host") or "").strip()
        if not value or any(c in value for c in "\r\n/@\\"):
            return False
        try:
            hostname = urllib.parse.urlsplit("//" + value).hostname
            port = urllib.parse.urlsplit("//" + value).port
        except ValueError:
            return False
        if not hostname or (port is not None and not 1 <= port <= 65535):
            return False
        hostname = hostname.rstrip(".").lower()
        allowed = {h.strip().lower().rstrip(".") for h in self.settings.allowed_hosts if h.strip()}
        allowed.update({"localhost", "127.0.0.1", "::1"})
        bind_host = self.settings.host.strip().strip("[]").lower()
        if bind_host not in {"0.0.0.0", "::", ""}:
            allowed.add(bind_host)
        try:
            address = ipaddress.ip_address(hostname)
            return address.is_loopback or hostname in allowed
        except ValueError:
            return hostname in allowed

    def _route(self, method: str) -> None:
        if not self._valid_host():
            self._error(400, "不正な Host ヘッダーです")
            return
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                # CORS preflight は認証ヘッダーを送れないため除外。実リクエストは全APIを保護。
                if method != "OPTIONS" and self.settings.room_token:
                    self._require_token()
                handler = self._api_route(method, path, query)
                if handler is None:
                    self._error(404, f"エンドポイントが見つかりません: {method} {path}")
                    return
                handler()
                return
            if method != "GET":
                self._error(405, "GET のみ対応しています")
                return
            self._serve_static(path)
        except ValueError as exc:
            self._error(400, str(exc))
        except FileNotFoundError as exc:
            self._error(404, str(exc) or "見つかりません")
        except PermissionError as exc:
            self._error(403, str(exc))
        except KeyError as exc:
            self._error(404, str(exc).strip("'"))
        except FileExistsError as exc:
            self._error(409, str(exc))
        except ProviderError as exc:
            self._error(exc.status or 502, f"Gemini API エラー: {exc}")
        except BrokenPipeError:
            self.close_connection = True
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    # ---- API ----
    def _api_route(self, method: str, path: str, query: dict[str, list[str]]) -> Callable[[], None] | None:
        parts = [p for p in posixpath.normpath(path).split("/") if p][1:]  # strip 'api'
        head = parts[0] if parts else ""

        if head == "health" and method == "GET":
            return self.api_health
        if head == "config" and method == "GET":
            return self.api_config
        if head == "key":
            if method == "GET":
                return self.api_key_status
            if method == "POST":
                return self.api_key_set
            if method == "DELETE":
                return self.api_key_clear
        if head == "models" and method == "GET":
            return self.api_models
        if head == "skills":
            rest = parts[1:]
            if not rest:
                if method == "GET":
                    return lambda: self.api_skills_list(query)
                if method == "POST":
                    return self.api_skill_create
            if len(rest) == 1:
                if rest[0] == "export" and method in {"GET", "POST"}:
                    return lambda: self.api_skills_export(query)
                if rest[0] == "draft" and method == "POST":
                    return self.api_skill_draft
                if method == "GET":
                    return lambda: self.api_skill_show(rest[0], query)
                if method == "PUT":
                    return lambda: self.api_skill_update(rest[0])
                if method == "DELETE":
                    return lambda: self.api_skill_delete(rest[0])
        if head == "sessions":
            rest = parts[1:]
            if not rest:
                if method == "GET":
                    return self.api_sessions_list
                if method == "POST":
                    return self.api_session_create
            if len(rest) == 1:
                if method == "GET":
                    return lambda: self.api_session_show(rest[0], query)
                if method == "DELETE":
                    return lambda: self.api_session_delete(rest[0])
        if head == "chat":
            if len(parts) == 1 and method == "POST":
                return lambda: self.api_chat(stream=False)
            if len(parts) == 2 and parts[1] == "stream" and method == "POST":
                return lambda: self.api_chat(stream=True)
        if head == "select" and method == "POST":
            return self.api_select
        if head == "rooms":
            rest = parts[1:]
            if not rest:
                if method == "GET":
                    return self.api_rooms_list
                if method == "POST":
                    return self.api_room_create
            if len(rest) == 1:
                if method == "GET":
                    return lambda: self.api_room_show(rest[0], query)
                if method == "DELETE":
                    return lambda: self.api_room_delete(rest[0])
            if len(rest) == 2 and rest[1] == "messages" and method == "POST":
                return lambda: self.api_room_post(rest[0])
        return None

    # -- 基本 --
    def api_health(self) -> None:
        engine = self.engine
        engine.library.reload()  # 外部でスキルを追加/削除しても件数がずれないように
        self._json({
            "ok": True,
            "version": __version__,
            "provider": engine.provider.name,
            "model": engine.provider.model,
            "simulated": engine.provider.is_mock,
            "api_key_set": bool(self.settings.api_key),
            "api_key_count": self.settings.api_key_count,
            "skills": len(engine.library),
            "skill_dirs": [str(d) for d in engine.library.dirs],
            "sessions": len(engine.store.list(limit=10000)),
            "rooms": len(self.rooms.list()),
            "tools": engine.tools.names(),
            "room_token_required": bool(self.settings.room_token),
            "webhook": self.webhooks.enabled,
            "webhook_stats": dict(self.webhooks.stats),
        })

    def api_config(self) -> None:
        settings = self.settings
        engine = self.engine
        data = settings.to_dict()
        data.update({
            "version": __version__,
            "known_models": KNOWN_MODELS,
            "simulated": engine.provider.is_mock,
            "skill_dirs_exist": [d.is_dir() for d in engine.library.dirs],
        })
        self._json(data)

    # -- API キー（私用なら UI から入れて保存できる） --
    def api_key_status(self) -> None:
        settings = self.settings
        pool = settings.key_pool()
        self._json({
            "has_key": bool(settings.api_key),
            "masked": mask_key(settings.api_key),
            "source": settings.api_key_source or ("none" if not settings.api_key else "unknown"),
            "file": str(settings.api_key_file) if settings.api_key_file else "",
            "key_count": pool.size,
            "keys_ready": pool.ready_count,
            "keys_dir": str(settings.keys_path),
            # 生のキーも指紋も返さない（伏せ字と出どころだけ）
            "keys": pool.status(),
            "stats": pool.stats()["totals"],     # 通算（429 で切れた回数など・キーは含まない）
            "provider": self.engine.provider.name,
            "model": self.engine.provider.model,
            "simulated": self.engine.provider.is_mock,
            "room_token_required": bool(settings.room_token),
        })

    def api_key_set(self) -> None:
        """``{"api_key": "AIza..."}`` を保存して、その場でプロバイダを切り替える。"""
        self._require_key_change()
        body = self._body()
        key = str(body.get("api_key") or "").strip()
        if not key:
            raise ValueError("api_key は必須です")
        if any(ch.isspace() for ch in key):
            raise ValueError("API キーに空白が含まれています（コピーし直してください）")
        if len(key) < 20:
            raise ValueError("API キーが短すぎます（Google AI Studio で発行したキーを貼り付けてください）")

        settings = self.settings
        path = write_api_key(settings, key)
        previous = self.engine.provider
        # いま入れたキーを最優先にする（既存のキーはそのまま予備として残す）
        settings.api_key = key
        settings.api_key_source = "file"
        if key not in settings.api_keys:
            settings.api_keys = [key, *settings.api_keys]
        if key not in {entry.value for entry in settings.key_entries}:
            settings.key_entries = [KeyEntry(key, "file", "", str(path)), *settings.key_entries]
        settings._key_pool = None      # 次の key_pool() で作り直す
        self.engine.provider = get_provider(settings)
        # 実 API で確認したいときだけ（既定はオフ＝速い）
        check: dict[str, Any] = {}
        if body.get("check"):
            try:
                models = self.engine.provider.model_catalog()
                if models:
                    check = {"ok": True, "models": models[:20], "model_known": settings.model in models}
                else:
                    check = {"ok": False, "error": "モデル一覧を取得できませんでした（キー / ネットワークを確認）"}
            except ProviderError as exc:
                check = {"ok": False, "error": str(exc)}
            if not check.get("ok"):
                # 繋がらないキーで会話を壊さない（保存はしたまま、シミュレータで続ける）
                self.engine.provider = previous
        self._json({
            "saved": str(path),
            "masked": mask_key(key),
            "provider": self.engine.provider.name,
            "model": self.engine.provider.model,
            "simulated": self.engine.provider.is_mock,
            "check": check or None,
        })

    def api_key_clear(self) -> None:
        """保存したキーを消す（環境変数 / .env のキーが残っていればそちらへ戻る）。"""
        self._require_key_change()
        settings = self.settings
        removed = clear_api_key(settings)
        # 保存ファイルと keys ディレクトリ由来のキーだけを落とす（環境変数 / .env は残す）
        remaining = [entry for entry in settings.key_entries if entry.source not in {"file", "keys"}]
        values = {entry.value for entry in remaining}
        if settings.api_key not in values:
            settings.api_key = remaining[0].value if remaining else ""
            settings.api_key_source = remaining[0].source if remaining else ""
        settings.key_entries = remaining
        settings.api_keys = [entry.value for entry in remaining]
        settings._key_pool = None
        self.engine.provider = get_provider(settings)
        self._json({
            "removed": removed,
            "key_count": self.engine.settings.key_pool().size,
            "provider": self.engine.provider.name,
            "simulated": self.engine.provider.is_mock,
        })

    def _require_key_change(self) -> None:
        """ルームのトークンを設定しているときは、キーの変更にも同じトークンを要求する。"""
        if self.settings.room_token:
            self._require_token()

    def api_models(self) -> None:
        provider = self.engine.provider
        models = provider.model_catalog()
        if not models:
            models = KNOWN_MODELS
        self._json({"models": models, "current": provider.model, "simulated": provider.is_mock})

    # -- スキル --
    def api_skills_list(self, query: dict[str, list[str]]) -> None:
        library = self.engine.library
        library.reload()
        q = (query.get("q") or [""])[0]
        payload: dict[str, Any] = {
            "skills": [s.to_dict(include_instructions=False) for s in library.all()],
            "errors": library.errors,
            "dirs": [str(d) for d in library.dirs],
        }
        if q:
            payload["matches"] = [{"name": s.name, "score": score} for s, score in library.search(q, limit=10, threshold=0.5)]
        self._json(payload)

    def api_skill_show(self, name: str, query: dict[str, list[str]]) -> None:
        skill = self.engine.library.get(slugify(name))
        if skill is None:
            raise KeyError(f"スキル '{name}' が見つかりません")
        payload = skill.to_dict()
        if (query.get("full") or ["1"])[0] in {"1", "true", "yes"}:
            from .prompt import estimate_tokens

            block = skill.instruction_block(self.settings.inline_reference_limit)
            payload["system_block"] = block
            payload["system_tokens"] = estimate_tokens(block)
        self._json(payload)

    def api_skill_create(self) -> None:
        body = self._body()
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("name は必須です")
        instructions = str(body.get("instructions") or body.get("body") or "")
        description = str(body.get("description") or "")
        meta = {k: body[k] for k in ("version", "license", "tags", "allowed-tools") if k in body}
        skill = self.engine.library.create(
            name, description, instructions, meta=meta, overwrite=bool(body.get("overwrite"))
        )
        self._json({"skill": skill.to_dict(), "path": str(skill.path)}, status=201)

    def api_skill_update(self, name: str) -> None:
        body = self._body()
        skill = self.engine.library.update(
            name,
            description=body.get("description"),
            instructions=body.get("instructions", body.get("body")),
            meta=body.get("meta"),
        )
        self._json({"skill": skill.to_dict(), "path": str(skill.path)})

    def api_skill_delete(self, name: str) -> None:
        deleted = self.engine.library.delete(name)
        if not deleted:
            raise KeyError(f"スキル '{name}' が見つかりません")
        self._json({"deleted": slugify(name)})

    def api_skill_draft(self) -> None:
        body = self._body()
        request = str(body.get("request") or "").strip()
        if not request:
            raise ValueError("request は必須です")
        draft = self.engine.draft_skill(request, context=str(body.get("context") or ""))
        self._json({"draft": draft.to_dict(), "markdown": draft.to_markdown()})

    def api_skills_export(self, query: dict[str, list[str]]) -> None:
        names: list[str] = []
        if self.command == "POST":
            body = self._body()
            names = [str(n) for n in body.get("names", [])]
        else:
            raw = (query.get("names") or [""])[0]
            names = [n for n in raw.split(",") if n]
        data = self.engine.library.export_zip(names or None)
        filename = f"{'-'.join(names)}.zip" if names else "actor-skills.zip"
        self._send(
            200, data, "application/zip",
            extra={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -- セッション --
    def api_sessions_list(self) -> None:
        self._json({"sessions": self.engine.store.list()})

    def api_session_create(self) -> None:
        body = self._body()
        session = self.engine.store.create(
            title=str(body.get("title") or "新しい会話"),
            skills=[str(s) for s in body.get("skills", [])],
            model=self.settings.model,
        )
        self._json({"session": session.to_dict()}, status=201)

    def api_session_show(self, session_id: str, query: dict[str, list[str]]) -> None:
        session = self.engine.store.require(session_id)
        include_thoughts = (query.get("thoughts") or ["1"])[0] not in {"0", "false"}
        self._json({"session": session.to_dict(include_thoughts=include_thoughts)})

    def api_session_delete(self, session_id: str) -> None:
        if not self.engine.store.delete(session_id):
            raise KeyError(f"セッション '{session_id}' が見つかりません")
        self._json({"deleted": session_id})

    # -- ルーム（常駐ボット用の共有ログ） --
    def api_rooms_list(self) -> None:
        self._json({"rooms": self.rooms.list(), "default": DEFAULT_ROOM})

    def api_room_create(self) -> None:
        self._require_token()
        body = self._body()
        name = str(body.get("name") or DEFAULT_ROOM).strip()
        if not RoomStore.valid_name(name):
            raise ValueError("ルーム名は英数字・ハイフン・アンダースコア 32 文字以内にしてください")
        store = self.rooms
        existed = store.get(name) is not None
        room = store.get_or_create(name, skills=[str(s) for s in body.get("skills", [])])
        self._json({"room": room.to_dict(after=0), "created": not existed}, status=200 if existed else 201)

    def api_room_show(self, name: str, query: dict[str, list[str]]) -> None:
        if self.settings.room_protect_reads:
            self._require_token()
        after = _int_param(query, "after", 0)
        limit = _int_param(query, "limit", 0)
        with_data = (query.get("attachments") or ["0"])[0] not in {"0", "false", ""}
        room = self.rooms.get(name)
        if room is None:
            raise KeyError(f"ルーム '{name}' が見つかりません")
        self._json(room.to_dict(after=after, limit=limit, include_attachment_data=with_data))

    def api_room_delete(self, name: str) -> None:
        self._require_token()
        if not self.rooms.delete(name):
            raise KeyError(f"ルーム '{name}' が見つかりません")
        self._json({"deleted": name})

    def api_room_post(self, name: str) -> None:
        """ルームへ発言する。``reply: true`` ならその場でモデルの返事も追記する。"""
        self._require_token()
        body = self._body()
        text = str(body.get("text") or "").strip()
        if not text:
            raise ValueError("text は必須です")
        store = self.rooms
        room = store.get_or_create(name, skills=[str(s) for s in body.get("skills", [])])
        role = "assistant" if body.get("role") in {"assistant", "bot", "model"} else "user"
        author = str(body.get("author") or ("Actor" if role == "assistant" else "user"))
        attachments = self._prepare_attachments(body.get("attachments"))
        tools = [str(t) for t in (body.get("tools") or []) if t]
        message = room.append(
            role, text, author=author,
            skills=[str(s) for s in body.get("skills", [])],
            model=str(body.get("model") or ""),
            simulated=bool(body.get("simulated")),
            usage=dict(body.get("usage") or {}),
            tools=tools,
            attachments=attachments,
        )

        reply_message = None
        missing_skills: list[str] = []
        if body.get("reply") and role == "user":
            reply_message, missing_skills = self._reply_in_room(room, text, body)

        store.save(room)
        self.webhooks.emit("message", {
            "room": room.name,
            "message": message.to_dict(),
            "reply": reply_message.to_dict() if reply_message else None,
            "count": len(room.messages),
        })
        self._json({
            "message": message.to_dict(),
            "reply": reply_message.to_dict() if reply_message else None,
            "missing_skills": missing_skills,
            "room": {"name": room.name, "cursor": room.cursor, "count": len(room.messages)},
        }, status=201)

    def _prepare_attachments(self, raw: Any) -> list[dict[str, Any]]:
        """base64 の添付を検証して、保存できる形に整える。"""
        if not raw:
            return []
        if not isinstance(raw, list):
            raise ValueError("attachments は配列で送ってください")
        limit = self.settings.max_attachment_bytes
        prepared: list[dict[str, Any]] = []
        for item in raw[: self.settings.max_attachments]:
            if not isinstance(item, dict) or not item.get("data"):
                continue
            data = str(item["data"])
            size = int(item.get("size") or 0) or (len(data) * 3) // 4
            if size > limit:
                raise ValueError(
                    f"添付 '{item.get('name') or 'file'}' が大きすぎます"
                    f"（{size / 1048576:.1f}MB / 上限 {limit / 1048576:.0f}MB）"
                )
            prepared.append({
                "name": str(item.get("name") or "file"),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                "size": size,
                "data": data if self.settings.store_attachments else "",
            })
        return prepared

    def _reply_in_room(self, room: Room, text: str, body: dict[str, Any]):
        """サーバー側でその場で返事を生成してルームへ追記する。

        Returns: (追記したメッセージ, 見つからなかったスキル名)
        """
        engine = self.engine
        session = engine.store.get(room.session_id) if room.session_id else None
        if session is None:
            session = engine.store.create(
                title=f"room:{room.name}", skills=list(room.skills), model=self.settings.model
            )
            room.session_id = session.id
        requested = [str(s) for s in body.get("skills", [])] or list(room.skills)
        _, missing = engine.library.resolve(requested)
        attachments = list(room.messages[-1].attachments) if room.messages else []
        done = engine.complete(
            session, text, explicit_skills=requested or None, attachments=attachments
        )
        message = engine.store.get(session.id).messages[-1]
        appended = room.append(
            "assistant", message.content, author=str(body.get("author") or "Actor"),
            skills=message.skills, model=message.model, simulated=message.simulated,
            usage=message.usage, tools=message.tools,
        )
        return appended, missing

    # -- 選択プレビュー --
    def api_select(self) -> None:
        body = self._body()
        query_text = str(body.get("message") or body.get("query") or "")
        explicit = [str(s) for s in body.get("skills", [])]
        auto = body.get("auto")
        selection = self.engine.select(
            query_text, explicit=explicit, auto=None if auto is None else bool(auto)
        )
        self._json(selection.to_dict(include_prompt=bool(body.get("include_prompt"))))

    # -- チャット --
    def _chat_args(self, body: dict[str, Any]) -> tuple[Any, str, list[str], bool | None, list[dict[str, Any]]]:
        message = str(body.get("message") or "").strip()
        if not message:
            raise ValueError("message は必須です")
        session_id = str(body.get("session_id") or "").strip()
        if session_id:
            session = self.engine.store.require(session_id)
        else:
            session = self.engine.store.create(
                title=str(body.get("title") or "新しい会話"),
                skills=[str(s) for s in body.get("skills", [])],
                model=self.settings.model,
            )
        explicit = [str(s) for s in body.get("skills", [])]
        auto = body.get("auto")
        attachments = self._prepare_attachments(body.get("attachments"))
        return session, message, explicit, (None if auto is None else bool(auto)), attachments

    def api_chat(self, stream: bool) -> None:
        body = self._body()
        session, message, explicit, auto, attachments = self._chat_args(body)
        if not stream:
            events = list(self.engine.turn(
                session, message, explicit_skills=explicit, auto=auto, stream=False,
                attachments=attachments,
            ))
            done = next((e for e in events if e["type"] == "done"), {})
            selection = next((e for e in events if e["type"] == "selection"), {})
            text = "".join(e.get("text", "") for e in events if e["type"] == "text")
            self._json({
                "session_id": session.id,
                "text": text,
                "thoughts": next((e.get("text", "") for e in events if e["type"] == "thought"), ""),
                "skills": selection.get("names", []),
                "selection": selection,
                "usage": done.get("usage", {}),
                "model": done.get("model", ""),
                "simulated": done.get("simulated", False),
                "error": done.get("error"),
                "system_instruction": next((e.get("system_instruction", "") for e in events if e["type"] == "prompt"), ""),
            })
            return

        # SSE
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", "frame-ancestors *")
        self._cors()
        self.end_headers()
        self.close_connection = True

        def emit(event: dict[str, Any]) -> None:
            payload = json.dumps(event, ensure_ascii=False, default=str)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            emit({"type": "open", "session_id": session.id, "model": self.engine.provider.model})
            for event in self.engine.turn(
                session, message, explicit_skills=explicit, auto=auto, stream=True,
                attachments=attachments,
            ):
                emit(event)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # pragma: no cover
            try:
                emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass

    # ---- 静的ファイル ----
    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        target = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())):
            self._error(403, "参照できません")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA フォールバック
            index = WEB_DIR / "index.html"
            if index.is_file() and "." not in posixpath.basename(path):
                target = index
            else:
                self._error(404, f"見つかりません: {path}")
                return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)


class ActorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], engine: ChatEngine, quiet: bool = False):
        super().__init__(address, ActorHandler)
        self.engine = engine
        self.quiet = quiet
        self.rooms = RoomStore(engine.settings.data_dir / "rooms")
        self.webhooks = WebhookDispatcher(
            url=engine.settings.webhook_url,
            secret=engine.settings.webhook_secret,
            events=engine.settings.webhook_events,
            timeout=engine.settings.webhook_timeout,
            on_log=None if quiet else (lambda msg: print(f"[actor] webhook: {msg}")),
        )


def run_server(settings: Settings, open_browser: bool = False, quiet: bool = False) -> int:
    bind_host = (settings.host or "127.0.0.1").strip().strip("[]").lower()
    loopback = bind_host == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(bind_host).is_loopback
    except ValueError:
        pass
    if not loopback and not settings.room_token:
        print("外部インターフェースへの bind には ACTOR_ROOM_TOKEN が必要です")
        return 2
    engine = ChatEngine(settings)
    try:
        httpd = ActorServer((settings.host, settings.port), engine, quiet=quiet)
    except OSError as exc:
        print(f"ポート {settings.port} で待ち受けできません: {exc}")
        return 1

    url = f"http://127.0.0.1:{settings.port}/"
    print(f"Actor サーバー起動: {url}")
    print(f"  プロバイダ : {engine.provider.name}"
          + ("  (シミュレータ: GEMINI_API_KEY 未設定)" if engine.provider.is_mock else f" / {engine.provider.model}"))
    print(f"  スキル     : {len(engine.library)} 件 ({', '.join(str(d) for d in engine.library.dirs)})")
    print("  Ctrl-C で停止")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止します…")
    finally:
        httpd.server_close()
    return 0
