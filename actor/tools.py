"""ボットに持たせる道具（function calling のツール群）。

スキルの ``allowed-tools`` で「そのスキルを使っているときだけ使える道具」を絞れる。
安全性のため、既定では **読み取り系だけ** を有効にし、書き込み・ネットワークは
設定で明示的に許可したときだけ使えるようにしている。

    ACTOR_TOOLS=read_file,list_files,now,list_skills,read_skill
    ACTOR_TOOLS_ROOT=./workspace      # 読み書きできる範囲（既定: カレント）
    ACTOR_TOOLS_ALLOW_WRITE=false     # write_file を許可するか
    ACTOR_TOOLS_ALLOW_NETWORK=false   # http_get を許可するか
"""

from __future__ import annotations

import fnmatch
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__all__ = ["Tool", "ToolResult", "ToolRegistry", "build_registry", "BUILTIN_TOOLS"]

MAX_READ_BYTES = 256 * 1024
MAX_WRITE_BYTES = 256 * 1024
MAX_LIST_ENTRIES = 200

JST = timezone(timedelta(hours=9))


@dataclass
class ToolResult:
    ok: bool
    content: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "content": self.content} if self.ok else {"ok": False, "error": self.error}

    @property
    def text(self) -> str:
        return self.content if self.ok else f"Error: {self.error}"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., ToolResult]
    dangerous: bool = False
    tags: tuple[str, ...] = ()

    def declaration(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def call(self, args: dict[str, Any]) -> ToolResult:
        try:
            return self.func(**args)
        except TypeError as exc:
            return ToolResult(False, error=f"Invalid arguments: {exc}")
        except Exception as exc:  # ツール内の失敗で会話を止めない
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}")


# ----------------------------------------------------------------------
# 個々の道具
# ----------------------------------------------------------------------
def _safe_path(root: Path, raw: str) -> Path:
    """root の外へ出るパスを弾く。"""
    if not raw or raw.strip() in {".", "./"}:
        return root
    candidate = (root / raw).expanduser() if not os.path.isabs(raw) else Path(raw).expanduser()
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PermissionError(f"Path is outside the allowed root: {raw} (root={root_resolved})")
    return resolved


def tool_now(timezone_name: str = "Asia/Tokyo") -> ToolResult:
    now = datetime.now(JST if timezone_name.endswith("Tokyo") else timezone.utc)
    return ToolResult(True, content=(
        f"{now.isoformat(timespec='seconds')} ({timezone_name})\n"
        f"Day: {now.strftime('%A')}"
    ))


def tool_read_file(root: Path) -> Callable[..., ToolResult]:
    def run(path: str, max_bytes: int = MAX_READ_BYTES) -> ToolResult:
        target = _safe_path(root, path)
        if not target.is_file():
            return ToolResult(False, error=f"File not found: {path}")
        size = target.stat().st_size
        limit = min(int(max_bytes or MAX_READ_BYTES), MAX_READ_BYTES)
        if size > limit:
            with target.open("r", encoding="utf-8", errors="replace") as handle:
                body = handle.read(limit)
            return ToolResult(True, content=f"[Showing first {limit} of {size} bytes]\n{body}")
        try:
            return ToolResult(True, content=target.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return ToolResult(False, error=str(exc))

    return run


def tool_list_files(root: Path) -> Callable[..., ToolResult]:
    def run(path: str = ".", pattern: str = "*", recursive: bool = False) -> ToolResult:
        base = _safe_path(root, path)
        if not base.exists():
            return ToolResult(False, error=f"Path not found: {path}")
        iterator = base.rglob(pattern) if recursive else base.glob(pattern)
        lines: list[str] = []
        for item in iterator:
            if len(lines) >= MAX_LIST_ENTRIES:
                lines.append("… (additional entries omitted)")
                break
            rel = item.relative_to(root) if root in item.resolve().parents or item.resolve() == root else item
            suffix = "/" if item.is_dir() else ""
            try:
                size = f"  {item.stat().st_size:>8,} B" if item.is_file() else ""
            except OSError:
                size = ""
            lines.append(f"{rel}{suffix}{size}")
        body = "\n".join(lines) or "(No matching items)"
        return ToolResult(True, content=f"root: {root}\n{body}")

    return run


def tool_write_file(root: Path) -> Callable[..., ToolResult]:
    def run(path: str, content: str) -> ToolResult:
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            return ToolResult(False, error=f"File is too large to write (limit: {MAX_WRITE_BYTES} bytes).")
        target = _safe_path(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, content=(
            f"{'Overwrote' if existed else 'Created'}: {target}"
            f" ({len(content.encode('utf-8')):,} bytes)"
        ))

    return run


def tool_http_get(timeout: float = 15.0) -> Callable[..., ToolResult]:
    def run(url: str, max_bytes: int = 100_000) -> ToolResult:
        if not re.match(r"^https?://", url or ""):
            return ToolResult(False, error="Only http(s) URLs are supported.")
        request = urllib.request.Request(url, headers={"User-Agent": "Actor/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(min(int(max_bytes or 100_000), 400_000))
                text = raw.decode("utf-8", errors="replace")
                return ToolResult(True, content=f"{response.status} {response.headers.get('Content-Type', '')}\n{text}")
        except urllib.error.HTTPError as exc:
            return ToolResult(False, error=f"{exc.code} {exc.reason}")
        except Exception as exc:
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}")

    return run


def tool_list_skills(get_skills: Callable[[], Sequence[Any]]) -> Callable[..., ToolResult]:
    def run(query: str = "") -> ToolResult:
        skills = list(get_skills())
        if query:
            needle = query.lower()
            skills = [s for s in skills if needle in s.name.lower() or needle in (s.description or "").lower()]
        if not skills:
            return ToolResult(True, content="(No matching skills.)")
        lines = [f"- {s.name}: {s.description}" for s in skills[:30]]
        return ToolResult(True, content="\n".join(lines))

    return run


def tool_read_skill(get_skills: Callable[[], Sequence[Any]], inline_limit: int = 24_000) -> Callable[..., ToolResult]:
    def run(name: str) -> ToolResult:
        from .skills import slugify

        target = slugify(name)
        for skill in get_skills():
            if skill.name == target:
                block = skill.instruction_block(inline_limit=inline_limit)
                return ToolResult(True, content=block)
        return ToolResult(False, error=f"Skill '{name}' not found.")

    return run


# ----------------------------------------------------------------------
# レジストリ
# ----------------------------------------------------------------------
BUILTIN_TOOLS: dict[str, dict[str, Any]] = {
    "now": {
        "description": "Get the current date and time when the request needs today, the weekday, or the current time.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone_name": {"type": "string", "description": "Time zone (default: Asia/Tokyo)"},
            },
        },
    },
    "read_file": {
        "description": "Read a text file inside the allowed directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the allowed directory (e.g. README.md)"},
                "max_bytes": {"type": "integer", "description": "Maximum number of bytes to read"},
            },
            "required": ["path"],
        },
    },
    "list_files": {
        "description": "List files inside the allowed directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Base directory (default: .)"},
                "pattern": {"type": "string", "description": "Glob pattern (default: *)"},
                "recursive": {"type": "boolean", "description": "Search recursively"},
            },
        },
    },
    "write_file": {
        "description": "Write a text file inside the allowed directory (overwriting existing files is permitted).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the allowed directory"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    "http_get": {
        "description": "Fetch text content from a URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL"},
                "max_bytes": {"type": "integer", "description": "Maximum number of bytes to fetch"},
            },
            "required": ["url"],
        },
    },
    "list_skills": {
        "description": "List the skills (SKILL.md files) loaded by Actor.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Optional search query"}},
        },
    },
    "read_skill": {
        "description": "Read a skill (SKILL.md) when you need to follow its instructions.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name (e.g. minutes-writer)"}},
            "required": ["name"],
        },
    },
}

DEFAULT_ENABLED = ("now", "read_file", "list_files", "list_skills", "read_skill")


class ToolRegistry:
    """有効なツールの集合。スキルの allowed-tools で更に絞り込める。"""

    def __init__(self, tools: Sequence[Tool] = ()):
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    # ---- 構築 ----
    @classmethod
    def build(
        cls,
        enabled: Iterable[str] | None = None,
        *,
        root: Path | str = ".",
        allow_write: bool = False,
        allow_network: bool = False,
        timeout: float = 15.0,
        skill_provider: Callable[[], Sequence[Any]] | None = None,
        inline_limit: int = 24_000,
        extra: Sequence[Tool] = (),
    ) -> "ToolRegistry":
        names = list(enabled) if enabled is not None else list(DEFAULT_ENABLED)
        root_path = Path(root).expanduser()
        tools: list[Tool] = []
        for name in names:
            name = name.strip()
            if not name:
                continue
            spec = BUILTIN_TOOLS.get(name)
            if spec is None:
                continue
            if name == "now":
                tools.append(Tool("now", spec["description"], spec["parameters"], tool_now))
            elif name == "read_file":
                tools.append(Tool("read_file", spec["description"], spec["parameters"], tool_read_file(root_path)))
            elif name == "list_files":
                tools.append(Tool("list_files", spec["description"], spec["parameters"], tool_list_files(root_path)))
            elif name == "write_file" and allow_write:
                tools.append(Tool("write_file", spec["description"], spec["parameters"],
                                  tool_write_file(root_path), dangerous=True))
            elif name == "http_get" and allow_network:
                tools.append(Tool("http_get", spec["description"], spec["parameters"],
                                  tool_http_get(timeout), dangerous=True))
            elif name in {"list_skills", "read_skill"} and skill_provider is not None:
                func = tool_list_skills(skill_provider) if name == "list_skills" else \
                    tool_read_skill(skill_provider, inline_limit)
                tools.append(Tool(name, spec["description"], spec["parameters"], func))
        tools.extend(extra)
        return cls(tools)

    # ---- 参照 ----
    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def subset(self, allowed: Sequence[str] | None) -> "ToolRegistry":
        """``allowed`` が指定されていれば、その名前だけに絞ったレジストリを返す。"""
        if not allowed:
            return self
        wanted = {a.strip().replace("-", "_") for a in allowed if a and a.strip()}
        keep = [tool for name, tool in self._tools.items() if name in wanted]
        if not keep:
            return ToolRegistry([])
        return ToolRegistry(keep)

    def declarations(self) -> list[dict[str, Any]]:
        return [tool.declaration() for tool in self._tools.values()]

    def to_rest_tools(self) -> list[dict[str, Any]]:
        """REST API の tools パラメータ（functionDeclarations）へ。"""
        if not self._tools:
            return []
        return [{"functionDeclarations": self.declarations()}]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, error=f"Tool '{name}' is not available (enabled: {', '.join(self.names()) or 'none'}).")
        return tool.call(dict(args or {}))

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "dangerous": t.dangerous}
            for t in self._tools.values()
        ]


def build_registry(settings: Any, skill_provider: Callable[[], Sequence[Any]] | None = None) -> ToolRegistry:
    """設定からツールレジストリを組み立てる。"""
    enabled = getattr(settings, "tools", None) or list(DEFAULT_ENABLED)
    return ToolRegistry.build(
        enabled,
        root=getattr(settings, "tools_root", "."),
        allow_write=bool(getattr(settings, "tools_allow_write", False)),
        allow_network=bool(getattr(settings, "tools_allow_network", False)),
        timeout=float(getattr(settings, "tools_timeout", 15.0)),
        skill_provider=skill_provider,
        inline_limit=int(getattr(settings, "inline_reference_limit", 24_000)),
    )


def parse_args(raw: Any) -> dict[str, Any]:
    """モデルが返す args を dict に正規化する（JSON 文字列で返ってくる場合がある）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"
