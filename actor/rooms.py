"""ルーム（常駐ボット用の会話ログ）。

「自分だけのクライアント／常駐ボット」を作るとき、ボットと人間が同じ場所を見るための
最小限の共有ログ。JSON ファイル1つ（``.actor/rooms/<name>.json``）で完結する。

- 追記のみ・単調増加の ``seq`` を持つので、``after=<seq>`` で差分ポーリングできる
- ボットは HTTP 越しでもローカルでも同じ API で読み書きできる
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOM_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

DEFAULT_ROOM = "main"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RoomMessage:
    seq: int
    role: str  # user | assistant
    text: str
    author: str = ""
    ts: str = field(default_factory=_now)
    skills: list[str] = field(default_factory=list)
    model: str = ""
    simulated: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)  # {name, mime_type, size, data}

    def to_dict(self, *, include_attachment_data: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_attachment_data:
            data["attachments"] = [
                {k: v for k, v in item.items() if k != "data"} for item in self.attachments
            ]
        return data


@dataclass
class Room:
    name: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    session_id: str = ""
    skills: list[str] = field(default_factory=list)
    messages: list[RoomMessage] = field(default_factory=list)

    @property
    def cursor(self) -> int:
        return self.messages[-1].seq if self.messages else 0

    def append(self, role: str, text: str, **kw: Any) -> RoomMessage:
        message = RoomMessage(seq=self.cursor + 1, role=role, text=text, **kw)
        self.messages.append(message)
        self.updated_at = _now()
        return message

    def since(self, after: int = 0) -> list[RoomMessage]:
        return [m for m in self.messages if m.seq > after]

    def to_dict(self, *, after: int = 0, limit: int = 0,
                include_attachment_data: bool = False) -> dict[str, Any]:
        messages = self.since(after)
        if limit > 0:
            messages = messages[-limit:]
        return {
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "session_id": self.session_id,
            "skills": list(self.skills),
            "cursor": self.cursor,
            "count": len(self.messages),
            "messages": [
                m.to_dict(include_attachment_data=include_attachment_data) for m in messages
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Room":
        room = cls(
            name=str(data.get("name") or DEFAULT_ROOM),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            session_id=str(data.get("session_id") or ""),
            skills=[str(s) for s in data.get("skills", [])],
        )
        for raw in data.get("messages", []):
            room.messages.append(RoomMessage(
                seq=int(raw.get("seq") or len(room.messages) + 1),
                role=str(raw.get("role") or "user"),
                text=str(raw.get("text") or ""),
                author=str(raw.get("author") or ""),
                ts=str(raw.get("ts") or _now()),
                skills=[str(s) for s in raw.get("skills", [])],
                model=str(raw.get("model") or ""),
                simulated=bool(raw.get("simulated", False)),
                usage=dict(raw.get("usage") or {}),
                tools=[str(t) for t in raw.get("tools", [])],
                attachments=[dict(a) for a in raw.get("attachments", []) if isinstance(a, dict)],
            ))
        return room


class RoomStore:
    """ルームを ``<data_dir>/rooms/<name>.json`` に保存する。"""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    # ---- パス ----
    def path_of(self, name: str) -> Path:
        if not ROOM_NAME_RE.match(name or ""):
            raise ValueError(
                "ルーム名は英数字・ハイフン・アンダースコア 32 文字以内にしてください"
            )
        return self.directory / f"{name}.json"

    @staticmethod
    def valid_name(name: str) -> bool:
        return bool(ROOM_NAME_RE.match(name or ""))

    # ---- CRUD ----
    def create(self, name: str = DEFAULT_ROOM, skills: list[str] | None = None,
               session_id: str = "") -> Room:
        path = self.path_of(name)
        if path.is_file():
            room = self.get(name)
            assert room is not None
            return room
        room = Room(name=name, skills=list(skills or []), session_id=session_id)
        self.save(room)
        return room

    def get_or_create(self, name: str, **kw: Any) -> Room:
        room = self.get(name)
        if room is None:
            room = self.create(name, **kw)
        return room

    def get(self, name: str) -> Room | None:
        path = self.path_of(name)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return Room.from_dict(data)

    def require(self, name: str) -> Room:
        room = self.get(name)
        if room is None:
            raise KeyError(f"ルーム '{name}' が見つかりません")
        return room

    def save(self, room: Room) -> Room:
        # 実行中にデータフォルダごと消された場合でも書き戻せるようにする
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path_of(room.name).write_text(
            json.dumps(room.to_dict(after=0, include_attachment_data=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return room

    def delete(self, name: str) -> bool:
        path = self.path_of(name)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            if not self.valid_name(path.stem):
                continue
            room = self.get(path.stem)
            if room is None:
                continue
            last = room.messages[-1].text if room.messages else ""
            items.append({
                "name": room.name,
                "created_at": room.created_at,
                "updated_at": room.updated_at,
                "skills": room.skills,
                "cursor": room.cursor,
                "count": len(room.messages),
                "preview": " ".join(last.split())[:120],
                "attachments": len(room.messages[-1].attachments) if room.messages else 0,
            })
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items
