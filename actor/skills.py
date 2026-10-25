"""SKILL.md の読み込み・検証・検索・書き出し。

Google Gemini の「スキル」は、Markdown ネイティブなオープン標準 **SKILL.md**
を採用している。本モジュールはその形式のフォルダを読み込み、
``system_instruction`` へ展開できる形に整える。

スキルフォルダの例::

    skills/
      weekly-report/
        SKILL.md          # 必須（フォルダの最上位）
        references/
          template.md     # 任意。テキストは system_instruction へ自動で同梱
          style.pdf       # 任意。画像/PDF は inline data として同梱

SKILL.md の書式::

    ---
    name: weekly-report
    description: 週次レポートを社内フォーマットで作成する
    ---

    # 手順
    1. ...
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SKILL_FILENAME = "SKILL.md"
MAX_REFERENCE_BYTES = 2 * 1024 * 1024  # 読み込みを諦めるサイズ
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".text", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".tsv",
    ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html",
    ".css", ".scss", ".toml", ".ini", ".cfg", ".conf", ".xml", ".sql", ".rb", ".go", ".rs",
    ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp", ".r", ".jl", ".lua", ".php", ".pl",
    ".dot", ".mermaid", ".txt",
}
IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
PDF_SUFFIX = {".pdf": "application/pdf"}

# system_instruction へインライン同梱する際の既定の上限
DEFAULT_INLINE_LIMIT = 24_000
DEFAULT_MEDIA_LIMIT = 4 * 1024 * 1024
MAX_MEDIA_PARTS = 8


# ----------------------------------------------------------------------
# frontmatter
# ----------------------------------------------------------------------
def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """`---` で囲まれた YAML 風 frontmatter を素朴にパースする。

    PyYAML に依存しないため、``key: value`` / ネストした ``key:`` ブロック /
    リスト（``- item``）程度のごく一般的な形のみサポートする。
    """
    if not text.startswith("\ufeff"):
        pass
    else:
        text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() not in {"---", "---\ufeff"}:
        return {}, text

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in {"---", "..."}:
            end = i
            break
    if end is None:
        return {}, text

    meta: dict[str, Any] = {}
    current_key: str | None = None
    current_nested: str | None = None
    current_list: list[str] | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" \t"))
        if indent and current_nested:
            bucket = meta.setdefault(current_nested, {})
            if not isinstance(bucket, dict):
                bucket = {}
                meta[current_nested] = bucket
            if stripped.startswith("- "):
                nested_key = current_key or "items"
                values = bucket.setdefault(nested_key, [])
                if not isinstance(values, list):
                    values = [values]
                    bucket[nested_key] = values
                values.append(_scalar(stripped[2:].strip().strip("'\"")))
            elif ":" in stripped:
                key, _, value = stripped.partition(":")
                current_key = key.strip()
                bucket[current_key] = _scalar(value.strip()) if value.strip() else []
            continue
        if stripped.startswith("- ") and current_key:
            item = _scalar(stripped[2:].strip().strip("'\""))
            if current_list is None:
                current_list = []
                meta[current_key] = current_list
            current_list.append(item)
            continue
        if ":" in raw and not raw.startswith((" ", "\t")):
            key, _, value = raw.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key
            current_list = None
            current_nested = key if key == "metadata" and not value else None
            if not value:
                meta.setdefault(key, {})
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta[key] = _scalar(value)
        elif current_key is not None:
            prev = meta.get(current_key)
            if isinstance(prev, str):
                meta[current_key] = (prev + " " + stripped).strip()
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    low = value.lower()
    if low in {"true", "false"}:
        return low == "true"
    if low in {"null", "~", ""}:
        return None
    if re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    if re.fullmatch(r"-?\d+\.\d+", value.strip()):
        return float(value.strip())
    return value.strip()


def dump_frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for subkey, subvalue in value.items():
                if isinstance(subvalue, (list, tuple)):
                    lines.append(f"  {subkey}:")
                    for item in subvalue:
                        lines.append(f"    - {item}")
                else:
                    lines.append(f"  {subkey}: {json.dumps(subvalue, ensure_ascii=False) if isinstance(subvalue, str) else subvalue}")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            text = str(value)
            if "\n" in text or text.strip() != text:
                text = json.dumps(text, ensure_ascii=False)
            lines.append(f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines) + "\n"


FENCE_RE = re.compile(r"^(`{3,})([a-zA-Z0-9_+\-]*)\n([\s\S]*?)\n?\1[ \t]*$", re.M)


def code_fence(text: str, minimum: int = 3) -> str:
    """テキスト内の最長バックティック連続より長いフェンスを返す（ネスト対策）。"""
    longest = 0
    for match in re.finditer(r"`{3,}", text or ""):
        longest = max(longest, len(match.group(0)))
    return "`" * max(minimum, longest + 1)


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """Markdown のフェンス付きコードブロックを (言語, 中身) で列挙する。"""
    return [(m.group(2), m.group(3)) for m in FENCE_RE.finditer(text or "")]


def slugify(name: str) -> str:
    """kebab-case のスキル名へ正規化する（Gemini の命名規則に合わせる）。"""
    text = unicodedata.normalize("NFKD", name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "skill"


# ----------------------------------------------------------------------
# 参照ファイル
# ----------------------------------------------------------------------
@dataclass
class Reference:
    """スキルに同梱された参照ファイル。"""

    path: Path
    relpath: str
    size: int
    kind: str  # text | image | pdf | binary
    mime_type: str = "text/plain"
    text: str = ""
    error: str = ""

    @property
    def inlinable(self) -> bool:
        return self.kind in {"text", "image", "pdf"} and not self.error

    def to_dict(self) -> dict[str, Any]:
        d = {"relpath": self.relpath, "size": self.size, "kind": self.kind, "mime_type": self.mime_type}
        if self.error:
            d["error"] = self.error
        return d


def classify(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image", IMAGE_SUFFIXES[suffix]
    if suffix in PDF_SUFFIX:
        return "pdf", PDF_SUFFIX[suffix]
    if suffix in TEXT_SUFFIXES:
        return "text", _text_mime(suffix)
    return "binary", "application/octet-stream"


def _text_mime(suffix: str) -> str:
    if suffix in {".json", ".jsonl"}:
        return "application/json"
    if suffix in {".yaml", ".yml"}:
        return "application/yaml"
    if suffix in {".csv", ".tsv"}:
        return "text/csv"
    if suffix == ".html":
        return "text/html"
    if suffix in {".xml", ".mermaid", ".dot"}:
        return "text/plain"
    return "text/plain"


def load_reference(path: Path, root: Path) -> Reference:
    try:
        size = path.stat().st_size
    except OSError as exc:  # pragma: no cover - 権限エラー等
        return Reference(path=path, relpath=str(path), size=0, kind="binary", error=str(exc))
    relpath = str(path.relative_to(root))
    kind, mime = classify(path)
    ref = Reference(path=path, relpath=relpath, size=size, kind=kind, mime_type=mime)

    if size > MAX_REFERENCE_BYTES:
        ref.error = f"Too large to load ({size} bytes)."
        return ref
    if kind == "text":
        try:
            ref.text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            ref.error = "could not be read as UTF-8 (treated as binary)"
            ref.kind = "binary"
        except OSError as exc:
            ref.error = str(exc)
    return ref


# ----------------------------------------------------------------------
# Skill
# ----------------------------------------------------------------------
@dataclass
class Skill:
    """1つのスキル（フォルダ or 単体の SKILL.md）。"""

    name: str
    description: str
    instructions: str
    path: Path
    root: Path
    meta: dict[str, Any] = field(default_factory=dict)
    references: list[Reference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = ""  # どの skills_dir から読んだか

    # ---- 基本情報 ----
    @property
    def version(self) -> str:
        metadata = self.meta.get("metadata") if isinstance(self.meta.get("metadata"), dict) else {}
        return str(self.meta.get("version") or metadata.get("version") or "")

    @property
    def license(self) -> str:
        return str(self.meta.get("license") or "")

    @property
    def tags(self) -> list[str]:
        metadata = self.meta.get("metadata") if isinstance(self.meta.get("metadata"), dict) else {}
        raw = self.meta.get("tags") or self.meta.get("keywords") or metadata.get("tags") or []
        if isinstance(raw, str):
            raw = [t.strip() for t in raw.split(",") if t.strip()]
        return [str(t).lower() for t in raw]

    @property
    def allowed_tools(self) -> list[str]:
        raw = self.meta.get("allowed-tools") or self.meta.get("allowed_tools") or []
        if isinstance(raw, str):
            raw = [t.strip() for t in raw.split(",") if t.strip()]
        return [str(t) for t in raw]

    # ---- 生成パラメータの上書き（frontmatter で指定できる） ----
    def _override(self, *keys: str) -> Any:
        for key in keys:
            if key in self.meta and self.meta[key] not in (None, ""):
                return self.meta[key]
        return None

    @property
    def temperature(self) -> float | None:
        value = self._override("temperature", "temp")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def top_p(self) -> float | None:
        value = self._override("top_p", "top-p", "topp")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def max_output_tokens(self) -> int | None:
        value = self._override("max_output_tokens", "max-output-tokens", "max_tokens")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def thinking_budget(self) -> int | None:
        value = self._override("thinking_budget", "thinking-budget")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def model_override(self) -> str:
        return str(self._override("model", "model_override", "model-override") or "")

    @property
    def greeting(self) -> str:
        return str(self._override("greeting", "opening") or "")

    @property
    def overrides(self) -> dict[str, Any]:
        """宣言されている上書きだけをまとめて返す。"""
        data: dict[str, Any] = {}
        if self.temperature is not None:
            data["temperature"] = self.temperature
        if self.top_p is not None:
            data["top_p"] = self.top_p
        if self.max_output_tokens is not None:
            data["max_output_tokens"] = self.max_output_tokens
        if self.thinking_budget is not None:
            data["thinking_budget"] = self.thinking_budget
        if self.model_override:
            data["model"] = self.model_override
        return data

    @property
    def text_references(self) -> list[Reference]:
        return [r for r in self.references if r.kind == "text" and r.text and not r.error]

    @property
    def media_references(self) -> list[Reference]:
        return [r for r in self.references if r.kind in {"image", "pdf"} and not r.error]

    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.instructions.encode("utf-8"))
        for ref in sorted(self.references, key=lambda r: r.relpath):
            h.update(ref.relpath.encode("utf-8"))
            h.update(str(ref.size).encode("utf-8"))
        return h.hexdigest()[:12]

    def to_dict(self, include_instructions: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "license": self.license,
            "tags": self.tags,
            "allowed_tools": self.allowed_tools,
            "overrides": self.overrides,
            "greeting": self.greeting,
            "path": str(self.path),
            "root": str(self.root),
            "source": self.source,
            "hash": self.content_hash(),
            "warnings": list(self.warnings),
            "references": [r.to_dict() for r in self.references],
            "instruction_chars": len(self.instructions),
            "instruction_lines": len(self.instructions.splitlines()),
        }
        if include_instructions:
            d["instructions"] = self.instructions
        return d

    # ---- system_instruction への展開 ----
    def instruction_block(self, inline_limit: int = DEFAULT_INLINE_LIMIT) -> str:
        """1つのスキルを、system_instruction に埋め込むテキストへ変換する。"""
        parts = [f"<skill name=\"{self.name}\">"]
        if self.description:
            parts.append(f"Description: {self.description}")
        parts.append("")
        parts.append(self.instructions.strip())

        inlined, omitted = self._inline_text_references(inline_limit)
        if inlined:
            parts.append("")
            parts.append("## Reference materials (included with this skill)")
            for relpath, text in inlined:
                parts.append(f"### Reference: {relpath}")
                fence = code_fence(text)
                parts.append(fence)
                parts.append(text.strip())
                parts.append(fence)
        if omitted:
            parts.append("")
            parts.append(
                "Note: The following reference files are too large to include in the system instruction. "
                f"Ask the user for their contents if needed: {', '.join(omitted)}"
            )
        parts.append("</skill>")
        return "\n".join(parts)

    def _inline_text_references(self, inline_limit: int) -> tuple[list[tuple[str, str]], list[str]]:
        inlined: list[tuple[str, str]] = []
        omitted: list[str] = []
        used = 0
        for ref in sorted(self.text_references, key=lambda r: r.relpath):
            remaining = inline_limit - used
            if remaining <= 0 or len(ref.text) > remaining:
                omitted.append(ref.relpath)
                continue
            used += len(ref.text)
            inlined.append((ref.relpath, ref.text))
        return inlined, omitted

    def media_parts(self, total_limit: int = DEFAULT_MEDIA_LIMIT, max_parts: int = MAX_MEDIA_PARTS) -> list[dict[str, Any]]:
        """画像 / PDF を Gemini の inlineData パートへ変換する。"""
        parts: list[dict[str, Any]] = []
        used = 0
        for ref in sorted(self.media_references, key=lambda r: r.relpath):
            if len(parts) >= max_parts:
                break
            if used + ref.size > total_limit:
                continue
            try:
                raw = ref.path.read_bytes()
            except OSError:
                continue
            import base64

            used += ref.size
            parts.append({
                "inlineData": {
                    "mimeType": ref.mime_type,
                    "data": base64.b64encode(raw).decode("ascii"),
                }
            })
        return parts

    # ---- 書き出し ----
    def to_skill_md(self) -> str:
        meta: dict[str, Any] = {"name": self.name}
        if self.description:
            meta["description"] = self.description
        for key in ("version", "license", "tags", "allowed-tools"):
            value = self.meta.get(key)
            if value not in (None, "", []):
                meta[key] = value
        meta.setdefault("generated-by", "Actor")
        return dump_frontmatter(meta) + "\n" + self.instructions.strip() + "\n"


def skill_from_dir(path: Path, source: str = "") -> Skill:
    """SKILL.md を含むフォルダから Skill を作る。"""
    skill_md = path / SKILL_FILENAME
    return _build_skill(skill_md, root=path, source=source)


def skill_from_file(path: Path, source: str = "") -> Skill:
    """単体の .md ファイルから Skill を作る。"""
    return _build_skill(path, root=path.parent, source=source)


def _build_skill(skill_md: Path, root: Path, source: str = "") -> Skill:
    warnings: list[str] = []
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except UnicodeDecodeError:
        raw = skill_md.read_text(encoding="utf-8", errors="replace")
        warnings.append("SKILL.md could not be decoded as strict UTF-8; invalid bytes were replaced.")

    meta, body = parse_frontmatter(raw)
    folder_name = root.name if root.name else skill_md.stem
    name = str(meta.get("name") or "").strip()
    if not name:
        name = slugify(folder_name)
        warnings.append(f"No frontmatter name found; using folder name '{name}'.")
    normalized = slugify(name)
    if name != normalized:
        warnings.append(f"Skill name '{name}' is not kebab-case; using '{normalized}'.")
        name = normalized

    description = str(meta.get("description") or meta.get("summary") or "").strip()
    if not description:
        description = _first_line(body) or f"{name} skill"
        warnings.append("No frontmatter description found; inferred one from the body.")

    instructions = body.strip()
    if not instructions:
        warnings.append("SKILL.md body is empty.")

    references: list[Reference] = []
    if root.is_dir():
        references = _collect_references(root, skill_md)
        house_principles = root.parent / "_house" / "principles.md"
        if house_principles.is_file() and not any(r.relpath == "references/principles.md" for r in references):
            principle = load_reference(house_principles, root.parent)
            principle.relpath = "references/principles.md"
            references.append(principle)

    return Skill(
        name=name,
        description=description,
        instructions=instructions,
        path=skill_md,
        root=root,
        meta=meta,
        references=references,
        warnings=warnings,
        source=source,
    )


def _first_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:160]
    return ""


def _collect_references(root: Path, skill_md: Path) -> list[Reference]:
    refs: list[Reference] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path == skill_md or path.name == SKILL_FILENAME:
            continue
        if any(part.startswith(".") or part in {"__pycache__", "tests"} for part in path.relative_to(root).parts):
            continue
        refs.append(load_reference(path, root))
    return refs


# ----------------------------------------------------------------------
# ライブラリ
# ----------------------------------------------------------------------
class SkillLibrary:
    """複数のディレクトリから SKILL.md を集めて管理する。"""

    def __init__(self, dirs: Sequence[Path | str] | None = None):
        self.dirs: list[Path] = [Path(d).expanduser() for d in (dirs or [])]
        self._skills: dict[str, Skill] = {}
        self._errors: list[dict[str, str]] = []
        self._fingerprint: tuple[tuple[str, float], ...] = ()
        self.reload()

    # ---- 読み込み ----
    def reload(self, force: bool = False) -> "SkillLibrary":
        fingerprint = self._compute_fingerprint()
        if not force and fingerprint == self._fingerprint and self._skills:
            return self
        skills: dict[str, Skill] = {}
        errors: list[dict[str, str]] = []
        for directory in self.dirs:
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                try:
                    if child.is_dir():
                        skill_md = child / SKILL_FILENAME
                        if not skill_md.is_file():
                            continue
                        skill = skill_from_dir(child, source=str(directory))
                    elif child.is_file() and child.suffix.lower() == ".md" and child.name != SKILL_FILENAME:
                        skill = skill_from_file(child, source=str(directory))
                    elif child.is_file() and child.name == SKILL_FILENAME:
                        skill = skill_from_file(child, source=str(directory))
                    else:
                        continue
                except Exception as exc:  # 1件の壊れたスキルで全体を落とさない
                    errors.append({"path": str(child), "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if skill.name in skills:
                    errors.append({
                        "path": str(skill.path),
                        "error": f"Duplicate skill name '{skill.name}'; using the first one found.",
                    })
                    continue
                skills[skill.name] = skill
        self._skills = dict(sorted(skills.items()))
        self._errors = errors
        self._fingerprint = fingerprint
        return self

    def _compute_fingerprint(self) -> tuple[tuple[str, float], ...]:
        entries: list[tuple[str, float]] = []
        for directory in self.dirs:
            if not directory.is_dir():
                continue
            try:
                entries.append((str(directory), directory.stat().st_mtime))
                for child in sorted(directory.iterdir()):
                    try:
                        entries.append((str(child), child.stat().st_mtime))
                        if child.is_dir():
                            entries.append((str(child / SKILL_FILENAME), _mtime(child / SKILL_FILENAME)))
                    except OSError:
                        continue
            except OSError:
                continue
        return tuple(entries)

    # ---- 参照 ----
    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):
        return iter(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return list(self._skills)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    @property
    def errors(self) -> list[dict[str, str]]:
        return list(self._errors)

    def summary(self) -> list[dict[str, Any]]:
        return [s.to_dict(include_instructions=False) for s in self._skills.values()]

    def resolve(self, names: Iterable[str]) -> tuple[list[Skill], list[str]]:
        found: list[Skill] = []
        missing: list[str] = []
        for raw in names:
            name = slugify(raw)
            skill = self._skills.get(name)
            if skill:
                found.append(skill)
            else:
                missing.append(raw)
        return found, missing

    # ---- 検索 ----
    def search(self, query: str, limit: int = 3, threshold: float = 2.0) -> list[tuple[Skill, float]]:
        """依頼文に合いそうなスキルを決定的にスコアリングして返す。"""
        query_lower = (query or "").lower()
        if not query_lower.strip():
            return []
        query_tokens = _tokens(query_lower)
        query_grams = _cjk_bigrams(query_lower)

        scored: list[tuple[Skill, float]] = []
        for skill in self._skills.values():
            score = 0.0
            name = skill.name.lower()
            # 明示的な呼び出し（/name, @name, "name スキル"）
            if re.search(r"[/@]" + re.escape(name) + r"\b", query_lower):
                score += 100.0
            if name in query_lower:
                score += 5.0
            for token in query_tokens:
                if token == name:
                    score += 5.0
                elif len(token) >= 3 and token in name:
                    score += 3.0
                elif len(token) >= 3 and name in token:
                    score += 2.0

            desc_lower = skill.description.lower()
            for token in query_tokens:
                if len(token) >= 2 and token in desc_lower:
                    score += 1.0
            desc_grams = _cjk_bigrams(desc_lower)
            overlap = query_grams & desc_grams
            if overlap:
                score += min(3.0, 0.5 * len(overlap))
            for tag in skill.tags:
                if tag and tag in query_lower:
                    score += 1.5
            if score > 0:
                scored.append((skill, round(score, 3)))

        scored.sort(key=lambda pair: (-pair[1], pair[0].name))
        return [pair for pair in scored if pair[1] >= threshold][:limit]

    def select(self, query: str, explicit: Sequence[str] | None = None,
               auto: bool = True, limit: int = 3, threshold: float = 2.0) -> tuple[list[Skill], list[str], list[tuple[str, float]]]:
        """明示指定 + 自動選択をまとめて解決する。

        Returns
        -------
        (skills, missing_explicit, auto_scores)
        """
        selected: list[Skill] = []
        missing: list[str] = []
        if explicit:
            found, missing = self.resolve(explicit)
            for skill in found:
                if skill not in selected:
                    selected.append(skill)
        auto_scores: list[tuple[str, float]] = []
        if auto:
            for skill, score in self.search(query, limit=limit, threshold=threshold):
                auto_scores.append((skill.name, score))
                if skill not in selected and len(selected) < limit:
                    selected.append(skill)
        return selected, missing, auto_scores

    # ---- 生成 / 更新 ----
    def primary_dir(self) -> Path:
        if not self.dirs:
            raise RuntimeError("No skills directory is configured.")
        return self.dirs[0]

    def create(self, name: str, description: str, instructions: str,
               meta: dict[str, Any] | None = None, overwrite: bool = False,
               directory: Path | None = None) -> Skill:
        slug = slugify(name)
        if not NAME_RE.match(slug):
            raise ValueError(f"Could not normalize skill name '{name}' to kebab-case.")
        base = Path(directory) if directory else self.primary_dir()
        target_dir = base / slug
        skill_md = target_dir / SKILL_FILENAME
        if skill_md.exists() and not overwrite:
            raise FileExistsError(f"Skill '{slug}' already exists: {skill_md}")
        target_dir.mkdir(parents=True, exist_ok=True)
        merged: dict[str, Any] = {"name": slug}
        if description:
            merged["description"] = description
        for key, value in (meta or {}).items():
            if value not in (None, "", []):
                merged[key] = value
        content = dump_frontmatter(merged) + "\n" + (instructions or "").strip() + "\n"
        skill_md.write_text(content, encoding="utf-8")
        self.reload(force=True)
        skill = self.get(slug)
        if skill is None:  # pragma: no cover
            raise RuntimeError("Could not load the created skill.")
        return skill

    def update(self, name: str, description: str | None = None, instructions: str | None = None,
               meta: dict[str, Any] | None = None) -> Skill:
        slug = slugify(name)
        skill = self.get(slug)
        if skill is None:
            raise KeyError(f"Skill '{slug}' not found.")
        merged = dict(skill.meta)
        merged["name"] = slug
        if description is not None:
            merged["description"] = description
        if meta:
            for key, value in meta.items():
                if value in (None, ""):
                    merged.pop(key, None)
                else:
                    merged[key] = value
        body = skill.instructions if instructions is None else instructions.strip()
        skill.path.write_text(dump_frontmatter(merged) + "\n" + body + "\n", encoding="utf-8")
        self.reload(force=True)
        updated = self.get(slug)
        assert updated is not None
        return updated

    def delete(self, name: str) -> bool:
        slug = slugify(name)
        skill = self.get(slug)
        if skill is None:
            return False
        managed = any(_is_relative_to(skill.root, d) for d in self.dirs)
        if not managed:
            raise PermissionError(f"Cannot delete '{slug}' because it is outside the managed skill directories.")
        import shutil

        if skill.root.is_dir():
            shutil.rmtree(skill.root)
        else:
            skill.path.unlink(missing_ok=True)
        self.reload(force=True)
        return True

    # ---- エクスポート ----
    def export_zip(self, names: Sequence[str] | None = None) -> bytes:
        """Gemini へアップロードできる ZIP（最上位に SKILL.md）を作る。"""
        targets = self.resolve(names)[0] if names else self.all()
        if not targets:
            raise KeyError("No skills selected.")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for skill in targets:
                prefix = "" if len(targets) == 1 else f"{skill.name}/"
                zf.writestr(prefix + SKILL_FILENAME, skill.to_skill_md())
                for ref in skill.references:
                    try:
                        data = ref.path.read_bytes()
                    except OSError:
                        continue
                    zf.writestr(prefix + ref.relpath.replace("\\", "/"), data)
        return buffer.getvalue()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ----------------------------------------------------------------------
# 自動選択のためのトークナイザ
# ----------------------------------------------------------------------
_WORD_RE = re.compile(r"[a-z0-9_\-]+")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]+")


def _tokens(text: str) -> set[str]:
    tokens = set(_WORD_RE.findall(text))
    for chunk in _CJK_RE.findall(text):
        tokens.add(chunk)
        for i in range(len(chunk) - 1):
            tokens.add(chunk[i:i + 2])
    return {t for t in tokens if t}


def _cjk_bigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for chunk in _CJK_RE.findall(text):
        if len(chunk) == 1:
            grams.add(chunk)
        for i in range(len(chunk) - 1):
            grams.add(chunk[i:i + 2])
    return grams


def resolve_overrides(skills: Sequence["Skill"]) -> tuple[dict[str, Any], dict[str, str]]:
    """適用順にスキルを見て、生成パラメータの実効値を決める。

    先に適用されたスキル（ユーザーが明示指定 → 自動選択の順）の宣言を優先する。
    Returns: (実効値, どのスキルが宣言したか)
    """
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for skill in skills:
        for key, value in skill.overrides.items():
            if key not in values:
                values[key] = value
                sources[key] = skill.name
    return values, sources


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
