"""会話セッションの保持と永続化（JSON ファイル）。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Message:
    role: str  # user | assistant
    content: str
    ts: str = field(default_factory=_now)
    skills: list[str] = field(default_factory=list)
    auto_skills: list[str] = field(default_factory=list)
    thoughts: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    simulated: bool = False
    attachments: list[dict[str, Any]] = field(default_factory=list)  # {name, mime_type, size, data}
    tools: list[str] = field(default_factory=list)  # 呼び出したツール名

    def to_dict(self, include_thoughts: bool = True, include_attachment_data: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_thoughts:
            data.pop("thoughts", None)
        if not include_attachment_data:
            data["attachments"] = [
                {k: v for k, v in attachment.items() if k != "data"} for attachment in self.attachments
            ]
        return data

    @property
    def attachment_names(self) -> list[str]:
        return [str(a.get("name") or "file") for a in self.attachments]


@dataclass
class Session:
    id: str
    title: str = "新しい会話"
    skills: list[str] = field(default_factory=list)      # 明示選択されたスキル
    auto_skills: list[str] = field(default_factory=list)  # 自動選択されたスキル
    model: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- 便利メソッド ----
    def add_user(self, content: str, **kw: Any) -> Message:
        message = Message(role="user", content=content, **kw)
        self.messages.append(message)
        self.touch()
        return message

    def add_assistant(self, content: str, **kw: Any) -> Message:
        message = Message(role="assistant", content=content, **kw)
        self.messages.append(message)
        self.touch()
        return message

    def touch(self) -> None:
        self.updated_at = _now()

    def auto_title(self, max_len: int = 28) -> str:
        for message in self.messages:
            if message.role == "user" and message.content.strip():
                text = " ".join(message.content.split())
                return text[:max_len] + ("…" if len(text) > max_len else "")
        return self.title

    @property
    def turn_count(self) -> int:
        return sum(1 for m in self.messages if m.role == "user")

    def to_dict(self, include_messages: bool = True, include_thoughts: bool = True) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "skills": list(self.skills),
            "auto_skills": list(self.auto_skills),
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
            "metadata": dict(self.metadata),
        }
        if include_messages:
            data["messages"] = [m.to_dict(include_thoughts=include_thoughts) for m in self.messages]
        return data

    def preview(self, limit: int = 160) -> str:
        for message in reversed(self.messages):
            if message.content.strip():
                text = " ".join(message.content.split())
                return text[:limit]
        return ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        session = cls(
            id=str(data.get("id") or new_session_id()),
            title=str(data.get("title") or "新しい会話"),
            skills=[str(s) for s in data.get("skills", [])],
            auto_skills=[str(s) for s in data.get("auto_skills", [])],
            model=str(data.get("model") or ""),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            metadata=dict(data.get("metadata") or {}),
        )
        for raw in data.get("messages", []):
            session.messages.append(Message(
                role=str(raw.get("role", "user")),
                content=str(raw.get("content", "")),
                ts=str(raw.get("ts") or _now()),
                skills=[str(s) for s in raw.get("skills", [])],
                auto_skills=[str(s) for s in raw.get("auto_skills", [])],
                thoughts=str(raw.get("thoughts") or ""),
                usage=dict(raw.get("usage") or {}),
                model=str(raw.get("model") or ""),
                simulated=bool(raw.get("simulated", False)),
                attachments=[dict(a) for a in raw.get("attachments", []) if isinstance(a, dict)],
                tools=[str(t) for t in raw.get("tools", [])],
            ))
        return session


class SessionStore:
    """セッションを `<data_dir>/sessions/<id>.json` に保存する。"""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}

    def path_of(self, session_id: str) -> Path:
        if not SESSION_ID_RE.match(session_id or ""):
            raise ValueError("不正なセッションIDです")
        return self.directory / f"{session_id}.json"

    def create(self, title: str = "新しい会話", skills: Iterable[str] = (), model: str = "") -> Session:
        session = Session(id=new_session_id(), title=title, skills=list(skills), model=model)
        self.save(session)
        return session

    def get(self, session_id: str) -> Session | None:
        if session_id in self._cache:
            return self._cache[session_id]
        path = self.path_of(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        session = Session.from_dict(data)
        self._cache[session.id] = session
        return session

    def require(self, session_id: str) -> Session:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"セッション '{session_id}' が見つかりません")
        return session

    def save(self, session: Session) -> Session:
        session.touch()
        # 実行中にデータフォルダごと消された場合でも書き戻せるようにする
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_of(session.id)
        path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._cache[session.id] = session
        return session

    def delete(self, session_id: str) -> bool:
        self._cache.pop(session_id, None)
        path = self.path_of(session_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            session = Session.from_dict(data)
            items.append({
                "id": session.id,
                "title": session.title,
                "skills": session.skills,
                "auto_skills": session.auto_skills,
                "model": session.model,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "turn_count": session.turn_count,
                "preview": session.preview(),
            })
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items[:limit]
