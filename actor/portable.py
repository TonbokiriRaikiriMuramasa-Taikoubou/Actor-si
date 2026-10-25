"""Portable Agent Skills export and conservative package-level linting."""
from __future__ import annotations

import io
import json
import re
import stat
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit

from .skills import SKILL_FILENAME, TEXT_SUFFIXES, Skill, parse_frontmatter

SOURCE_URL = "https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si"
_PACKAGE_RESOURCES = Path(__file__).resolve().parent / "resources"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resource_path(name: str) -> Path:
    source_path = (_REPO_ROOT / "skills" / "_house" / "principles.md" if name == "principles.md"
                   else _REPO_ROOT / "LICENSE")
    packaged_path = _PACKAGE_RESOURCES / name
    if source_path.is_file():
        return source_path
    return packaged_path


PRINCIPLES_PATH = _resource_path("principles.md")
LICENSE_PATH = _resource_path("LICENSE")


@dataclass
class Finding:
    category: str
    severity: str
    message: str
    file: str = "SKILL.md"


@dataclass
class LintReport:
    skill: str
    score: int
    passed: bool
    findings: list[Finding]
    external_urls: list[str]
    external_domains: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "score": self.score,
            "passed": self.passed,
            "failures": sum(f.severity == "fail" for f in self.findings),
            "warnings": sum(f.severity == "warning" for f in self.findings),
            "findings": [asdict(f) for f in self.findings],
            "external_urls": self.external_urls,
            "external_domains": self.external_domains,
        }


def _urls(text: str) -> list[str]:
    return sorted(set(re.findall(r"https?://[^\s\])}>\"']+", text)))


def _is_project_source(url: str) -> bool:
    parsed = urlsplit(url)
    source = urlsplit(SOURCE_URL)
    return (parsed.netloc.lower() == source.netloc.lower()
            and (parsed.path.rstrip("/") == source.path.rstrip("/")
                 or parsed.path.startswith(source.path.rstrip("/") + "/")))


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _strip_html_comments_outside_fences(text: str) -> str:
    """Remove author comments from Markdown prose, preserving fenced examples verbatim."""
    output: list[str] = []
    prose: list[str] = []
    fence_char = ""
    fence_size = 0

    def flush_prose() -> None:
        if not prose:
            return
        output.append(re.sub(r"<!--.*?-->", "", "".join(prose), flags=re.DOTALL))
        prose.clear()

    for line in text.splitlines(keepends=True):
        plain = line.rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", plain)
        if fence_char:
            output.append(line)
            if (marker and marker.group(1)[0] == fence_char
                    and len(marker.group(1)) >= fence_size and not marker.group(2).strip()):
                fence_char = ""
                fence_size = 0
        elif marker:
            flush_prose()
            output.append(line)
            fence_char = marker.group(1)[0]
            fence_size = len(marker.group(1))
        elif line.startswith("    ") or line.startswith(chr(9)):
            flush_prose()
            output.append(line)
        else:
            prose.append(line)
    flush_prose()
    return "".join(output)


def _portable_document(skill: Skill, keep_allowed_tools: bool = False) -> str:
    original_metadata = skill.meta.get("metadata") if isinstance(skill.meta.get("metadata"), dict) else {}
    source_meta: dict[str, Any] = {"source": SOURCE_URL}
    version = skill.meta.get("version") or original_metadata.get("version")
    tags = skill.meta.get("tags") or original_metadata.get("tags")
    if version:
        source_meta["version"] = version
    if tags:
        source_meta["tags"] = tags

    lines = ["---", f"name: {skill.name}", f"description: {_yaml_scalar(skill.description)}"]
    if skill.license:
        lines.append(f"license: {skill.license}")
    when_to_use = skill.meta.get("when_to_use")
    if when_to_use:
        lines.append(f"when_to_use: {_yaml_scalar(when_to_use)}")
    if keep_allowed_tools and skill.allowed_tools:
        lines.append("allowed-tools:")
        lines.extend(f"  - {_yaml_scalar(tool)}" for tool in skill.allowed_tools)
    lines.append("metadata:")
    for key, value in source_meta.items():
        if isinstance(value, (list, tuple)):
            lines.append(f"  {key}:")
            lines.extend(f"    - {_yaml_scalar(item)}" for item in value)
        else:
            lines.append(f"  {key}: {_yaml_scalar(value)}")
    lines.append("---")

    body = _strip_html_comments_outside_fences(skill.instructions).strip()
    greeting = skill.meta.get("greeting")
    if greeting:
        body = f"## First response\nUse this greeting for the first reply: {greeting}\n\n{body}".strip()
    if skill.allowed_tools and not keep_allowed_tools:
        body += "\n\n## File access\nRead relevant files as needed. Do not modify files unless the user explicitly asks for changes."
    return "\n".join(lines) + "\n\n" + body + "\n"


def _portable_files(skill: Skill, keep_allowed_tools: bool = False) -> tuple[dict[str, bytes], list[str]]:
    if not PRINCIPLES_PATH.is_file():
        raise FileNotFoundError("portable shared principles resource is missing")
    if not LICENSE_PATH.is_file():
        raise FileNotFoundError("MIT LICENSE resource is missing")
    if not skill.name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill.name):
        raise ValueError("skill name must use lowercase letters, digits, and hyphens")

    prefix = f"{skill.name}/"
    files: dict[str, bytes] = {prefix + SKILL_FILENAME: _portable_document(skill, keep_allowed_tools).encode("utf-8")}
    skipped: list[str] = []
    for ref in sorted(skill.references, key=lambda item: item.relpath):
        relative = ref.relpath.replace(chr(92), "/")
        components = relative.split("/")
        if any(part.startswith(".") or part in {"__pycache__", "tests"} for part in components):
            continue
        if ref.kind != "text":
            skipped.append(relative)
            continue
        if ref.size > 50 * 1024 * 1024:
            skipped.append(relative)
            continue
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ValueError(f"unsafe reference path: {relative}")
        resolved: Path | None = None
        try:
            resolved = ref.path.resolve()
            resolved.relative_to(skill.root.resolve())
        except (ValueError, OSError):
            is_shared_principles = relative == "references/principles.md" and resolved == PRINCIPLES_PATH.resolve()
            if not is_shared_principles:
                raise ValueError(f"reference escapes skill folder: {relative}")
        archive_name = prefix + str(pure)
        if archive_name in files:
            raise ValueError(f"duplicate archive path: {archive_name}")
        files[archive_name] = ref.path.read_bytes()

    principles_name = prefix + "references/principles.md"
    files[principles_name] = PRINCIPLES_PATH.read_bytes()
    license_name = prefix + "LICENSE"
    if license_name in files:
        raise ValueError("skill references may not override the MIT LICENSE")
    files[license_name] = LICENSE_PATH.read_bytes()
    return files, skipped


def _write_deterministic_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


_SECRET_PATTERNS = (
    r"\bAIza[0-9A-Za-z_-]{20,}\b",
    r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"(?i)\b(?:api[_-]?key|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{20,}",
)
_PROMPT_INJECTION = (
    r"(?i)ignore (?:all )?(?:previous|prior) instructions",
    r"(?i)reveal (?:the )?(?:system prompt|secrets)",
    r"(?i)(?:without (?:telling|notifying|informing) (?:the )?user|do not (?:tell|notify|inform) (?:the )?user|never tell (?:the )?user|hide (?:this )?from (?:the )?user|conceal from (?:the )?user|secretly send)",
    r"(?i)(?:secretly|silently|covertly)\s+(?:send|upload|exfiltrate|execute|run)",
    r"(?i)(?:send|upload|exfiltrate|execute|run)\b.{0,80}\bwithout (?:telling|notifying|informing) (?:the )?user",
    r"(?:前の|以前の)指示を無視",
    r"(?:ユーザーに伝えない|ユーザーには知らせずに|ユーザーに知らせずに|ユーザーに黙って|秘密裏に(?:送信|実行)|こっそり(?:送信|実行)|知らせずに送信|隠して(?:実行|送信|おく))",
    r"(?i)\b(?:without (?:the user's )?knowledge|without consent|undisclosed|under the radar)\b",
    r"(?i)\b(?:silently|secretly|covertly)\s+(?:upload|transmit|exfiltrate|execute|run)",
    r"(?:ユーザーに)?(?:知らせないで|伝えないで|通知しないで|報告しないで)",
    r"(?:黙って|無断で|秘密裏に|こっそり)(?:アップロード|送信|実行|実行する|起動)",
    r"(?:報告|通知|連絡)せず(?:に)?(?:実行|アップロード|送信|実行する)",
)


def _lint_archive(skill: Skill, archive_data: bytes, skipped: list[str] | None = None) -> LintReport:
    findings: list[Finding] = []

    def add(category: str, severity: str, message: str, file: str = "SKILL.md") -> None:
        findings.append(Finding(category, severity, message, file))

    expected_prefix = f"{skill.name}/"
    file_text: dict[str, str] = {}
    urls: list[str] = []
    names: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(archive_data), "r") as archive:
            names = archive.namelist()
            if not names or any(not name.startswith(expected_prefix) for name in names):
                add("structure", "fail", "ZIP entries must be contained in the skill-named folder")
            if len(names) >= 1000:
                add("limits", "fail", "ZIP must contain fewer than 1,000 files")
            if len(archive_data) > 50 * 1024 * 1024:
                add("limits", "fail", "ZIP exceeds 50 MB")
            expected_skill = expected_prefix + SKILL_FILENAME
            if expected_skill not in names:
                add("structure", "fail", "SKILL.md is missing from the skill folder")
            for name in names:
                member = archive.getinfo(name)
                relative = name[len(expected_prefix):] if name.startswith(expected_prefix) else name
                if member.file_size > 50 * 1024 * 1024:
                    add("limits", "fail", "file exceeds 50 MB", relative)
                if relative in {"LICENSE", "SKILL.md"} or PurePosixPath(relative).suffix.lower() in TEXT_SUFFIXES:
                    try:
                        file_text[relative] = archive.read(name).decode("utf-8")
                    except UnicodeDecodeError:
                        add("file-types", "fail", "packaged text file is not UTF-8", relative)
                elif relative:
                    add("file-types", "fail", "unsupported or binary file in ZIP", relative)
    except (OSError, zipfile.BadZipFile) as exc:
        add("structure", "fail", f"invalid ZIP: {exc}")

    document = file_text.get(SKILL_FILENAME, "")
    meta, _ = parse_frontmatter(document)
    name = str(meta.get("name") or "")
    description = str(meta.get("description") or "")
    if not name or len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        add("structure", "fail", "name must be at most 64 lowercase letters, digits, and hyphens")
    if name != skill.name:
        add("structure", "fail", "frontmatter name does not match the ZIP folder name")
    if skill.root.name != skill.name:
        add("structure", "fail", f"source folder '{skill.root.name}' does not match name '{skill.name}'")
    if not description or len(description.split()) < 5:
        add("description", "fail", "description is missing or too short")
    else:
        sentence_count = len([part for part in re.split(r"(?<=[.!?])\s+", description.strip()) if part])
        if not 1 <= sentence_count <= 3:
            add("description", "fail", "description must contain one to three sentences")
        if not re.search(r"\buse when\b", description, re.I):
            add("description", "fail", "description must explain when to use it (Use when …)")
        if not re.search(r"[A-Za-z]{3,}", description):
            add("description", "fail", "description must include English text")
    if not meta.get("license"):
        add("metadata", "warning", "license is not declared")
    if expected_prefix + "LICENSE" not in names:
        add("metadata", "fail", "MIT LICENSE is missing from the ZIP")
    if expected_prefix + "references/principles.md" not in names:
        add("metadata", "fail", "shared principles reference is missing from the ZIP")

    all_text = "\n".join(file_text.values())
    urls = _urls(all_text)
    external_urls = [url for url in urls if not _is_project_source(url)]
    external_domains = sorted({urlsplit(url).hostname or "" for url in external_urls if urlsplit(url).hostname})

    checks = [
        ("dangerous-command", r"(?i)(?:\brm\s+-rf\b|curl[^\n|]*\|\s*(?:sh|bash)|\bsudo\b|\bmkfs\b|\bdd\s+if=)", "potentially destructive or unsafe command"),
        ("environment-collection", r"(?i)(?:printenv|env\s*\|\s*(?:curl|wget)|process\.env|os\.environ|dump.{0,20}environment)", "possible environment-variable collection"),
        ("obfuscation", r"(?i)(?:eval\s*\(|base64\s+(-d|--decode)|fromCharCode\s*\()", "possible obfuscated execution"),
    ]
    for category, pattern, message in checks:
        for relative, content in file_text.items():
            if re.search(pattern, content):
                add(category, "fail", message, relative)
                break
    for relative, content in file_text.items():
        if any(re.search(pattern, content) for pattern in _SECRET_PATTERNS):
            add("secrets", "fail", "possible credential, API key, or private key", relative)
        if any(re.search(pattern, content) for pattern in _PROMPT_INJECTION):
            add("prompt-injection", "fail", "possible prompt injection or concealment instruction", relative)
    for url in external_urls:
        add("external-url", "warning", f"review external URL: {url}")
    for relative in (skipped or []):
        add("omitted-reference", "warning", "reference was excluded from the portable ZIP", relative)

    score = max(0, 100 - 20 * sum(f.severity == "fail" for f in findings)
                - 5 * sum(f.severity == "warning" for f in findings))
    failed = any(f.severity == "fail" for f in findings)
    return LintReport(skill.name, score, not failed and score >= 80, findings, external_urls, external_domains)


def lint_skill(skill: Skill, keep_allowed_tools: bool = False) -> LintReport:
    """Build and scan the exact ZIP payload that would be exported for this skill."""
    try:
        files, skipped = _portable_files(skill, keep_allowed_tools=keep_allowed_tools)
        archive_data = _write_deterministic_zip(files)
    except (OSError, ValueError) as exc:
        finding = Finding("packaging", "fail", str(exc))
        return LintReport(skill.name, 80, False, [finding], [], [])
    return _lint_archive(skill, archive_data, skipped)


def lint_skills(skills: Iterable[Skill], keep_allowed_tools: bool = False) -> list[LintReport]:
    return [lint_skill(skill, keep_allowed_tools=keep_allowed_tools) for skill in skills]


def export_zip(skills: Iterable[Skill], keep_allowed_tools: bool = False,
               force: bool = False) -> tuple[bytes, list[LintReport]]:
    targets = list(skills)
    if len(targets) != 1:
        raise ValueError("portable/agensi export accepts exactly one skill")
    skill = targets[0]
    if skill.root.name != skill.name:
        raise ValueError(f"folder/name mismatch: {skill.root.name} != {skill.name}")
    try:
        files, skipped = _portable_files(skill, keep_allowed_tools=keep_allowed_tools)
    except (OSError, ValueError) as exc:
        raise ValueError(f"portable export resources or files are invalid: {exc}") from exc
    data = _write_deterministic_zip(files)
    reports = [_lint_archive(skill, data, skipped)]
    if not reports[0].passed and not force:
        raise ValueError("Agensi lint failed; export blocked (use --force to override)")
    if len(data) > 50 * 1024 * 1024 and not force:
        raise ValueError("ZIP exceeds 50 MB")
    return data, reports
