"""Portable Agent Skills export and conservative local linting (stdlib only)."""
from __future__ import annotations

import io
import json
import re
import zipfile
from urllib.parse import urlsplit
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

from .skills import Skill, dump_frontmatter, parse_frontmatter

SOURCE_URL = "https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor"
PRINCIPLES_PATH = Path(__file__).resolve().parent.parent / "skills" / "_house" / "principles.md"

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
        return {"skill": self.skill, "score": self.score, "passed": self.passed,
                "failures": sum(f.severity == "fail" for f in self.findings),
                "warnings": sum(f.severity == "warning" for f in self.findings),
                "findings": [asdict(f) for f in self.findings], "external_urls": self.external_urls,
                "external_domains": self.external_domains}


def _urls(text: str) -> list[str]:
    return sorted(set(re.findall(r"https?://[^\s\])}>\"']+", text)))


def lint_skill(skill: Skill) -> LintReport:
    findings: list[Finding] = []
    def add(category: str, severity: str, message: str, file: str = "SKILL.md") -> None:
        findings.append(Finding(category, severity, message, file))
    body = skill.instructions or ""
    text = skill.to_skill_md()
    scan_text = text + "\n" + "\n".join(ref.text for ref in skill.references if ref.kind == "text")
    urls = _urls(scan_text)
    if not skill.name or len(skill.name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill.name):
        add("structure", "fail", "name must be at most 64 lowercase letters, digits, and hyphens")
    if skill.root.name != skill.name:
        add("structure", "fail", f"folder name '{skill.root.name}' does not match name '{skill.name}'")
    if not skill.description or len(skill.description.split()) < 5:
        add("description", "fail", "description is missing or too short")
    else:
        sentence_count = len([x for x in re.split(r"(?<=[.!?])\s+", skill.description.strip()) if x])
        if not 1 <= sentence_count <= 3:
            add("description", "fail", "description must contain one to three sentences")
        if not re.search(r"\buse when\b", skill.description, re.I):
            add("description", "fail", "description must explain when to use it (Use when …)")
        if not re.search(r"[A-Za-z]{3,}", skill.description):
            add("description", "fail", "description must include an English sentence")
    if not skill.license:
        add("metadata", "warning", "license is not declared")
    binary = [ref for ref in skill.references if ref.kind != "text"]
    for ref in binary:
        add("file-types", "fail", "binary or non-text reference files are not portable", ref.relpath)
    for ref in skill.references:
        if ref.size > 50 * 1024 * 1024:
            add("limits", "fail", "file exceeds 50 MB", ref.relpath)
    portable_refs = [ref for ref in skill.references if ref.kind == "text" and not any(
        part.startswith(".") or part in {"__pycache__", "tests"} for part in ref.relpath.replace(chr(92), "/").split("/")
    )]
    has_principles = any(ref.relpath == "references/principles.md" for ref in portable_refs)
    files = 2 + len(portable_refs) + (0 if has_principles else 1)  # SKILL.md, LICENSE, principles, references
    if files >= 1000:
        add("limits", "fail", "ZIP must contain fewer than 1,000 files")
    checks = [
        ("dangerous-command", r"(?i)(?:\brm\s+-rf\b|curl[^\n|]*\|\s*(?:sh|bash)|\bsudo\b|\bmkfs\b|\bdd\s+if=)", "potentially destructive or unsafe command"),
        ("secrets", r"(?i)(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:api[_-]?key|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{20,})", "possible credential or private key"),
        ("environment-collection", r"(?i)(?:printenv|env\s*\|\s*(?:curl|wget)|process\.env|os\.environ|dump.{0,20}environment)", "possible environment-variable collection"),
        ("obfuscation", r"(?i)(?:eval\s*\(|base64\s+(-d|--decode)|fromCharCode\s*\()", "possible obfuscated execution"),
        ("prompt-injection", r"(?i)(?:ignore (?:all )?(?:previous|prior) instructions|reveal (?:the )?(?:system prompt|secrets)|(?:前の|以前の)指示を無視|ユーザーに伝えない|隠しておく)", "prompt injection or concealment language"),
    ]
    for category, pattern, message in checks:
        match = re.search(pattern, scan_text)
        if match:
            add(category, "fail", message)
    external = [url for url in urls if "github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor" not in url]
    for url in external:
        add("external-url", "warning", f"review external URL: {url}")
    score = max(0, 100 - 20 * sum(f.severity == "fail" for f in findings) - 5 * sum(f.severity == "warning" for f in findings))
    failed = any(f.severity == "fail" for f in findings)
    domains = sorted({urlsplit(url).hostname or "" for url in external if urlsplit(url).hostname})
    return LintReport(skill.name, score, not failed and score >= 80, findings, urls, domains)


def lint_skills(skills: Iterable[Skill]) -> list[LintReport]:
    return [lint_skill(skill) for skill in skills]


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _portable_document(skill: Skill, keep_allowed_tools: bool) -> str:
    original_metadata = skill.meta.get("metadata") if isinstance(skill.meta.get("metadata"), dict) else {}
    source_meta: dict[str, Any] = {"source": SOURCE_URL}
    version = skill.meta.get("version") or original_metadata.get("version")
    tags = skill.meta.get("tags") or original_metadata.get("tags")
    if version:
        source_meta["version"] = version
    if tags:
        source_meta["tags"] = tags
    keys = ["name", "description"]
    meta: dict[str, Any] = {"name": skill.name, "description": skill.description}
    if skill.license:
        meta["license"] = skill.license
    if skill.meta.get("when_to_use"):
        meta["when_to_use"] = skill.meta["when_to_use"]
    if keep_allowed_tools and skill.allowed_tools:
        meta["allowed-tools"] = skill.allowed_tools
    lines = ["---", f"name: {skill.name}", f"description: {_yaml_scalar(skill.description)}"]
    if skill.license:
        lines.append(f"license: {skill.license}")
    if skill.meta.get("when_to_use"):
        lines.append(f"when_to_use: {_yaml_scalar(skill.meta['when_to_use'])}")
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
    body = skill.instructions.strip()
    greeting = skill.meta.get("greeting")
    if greeting:
        body = f"## First response\nUse this greeting for the first reply: {greeting}\n\n{body}".strip()
    dropped = [k for k in ("temperature", "top_p", "max_output_tokens", "thinking_budget", "model") if skill.meta.get(k) not in (None, "")]
    if dropped:
        body = "<!-- Actor-specific generation settings omitted from portable export: " + ", ".join(dropped) + " -->\n\n" + body
    if skill.allowed_tools and not keep_allowed_tools:
        safe_note = "\n\n## File access\nRead relevant files as needed. Do not modify files unless the user explicitly asks for changes."
        body += safe_note
    return "\n".join(lines) + "\n\n" + body + "\n"


def export_zip(skills: Iterable[Skill], keep_allowed_tools: bool = False, force: bool = False) -> tuple[bytes, list[LintReport]]:
    targets = list(skills)
    reports = lint_skills(targets)
    failures = [report for report in reports if not report.passed]
    if failures and not force:
        raise ValueError("Agensi lint failed; export blocked (use --force to override)")
    if not targets:
        raise ValueError("対象のスキルがありません")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for skill in targets:
            if skill.root.name != skill.name:
                raise ValueError(f"folder/name mismatch: {skill.root.name} != {skill.name}")
            prefix = f"{skill.name}/"
            zf.writestr(prefix + "SKILL.md", _portable_document(skill, keep_allowed_tools))
            for ref in skill.references:
                components = ref.relpath.replace(chr(92), "/").split("/")
                if (ref.kind != "text" or any(part.startswith(".") or part in {"__pycache__", "tests"} for part in components)):
                    continue
                try:
                    ref.path.resolve().relative_to(skill.root.resolve())
                except (ValueError, OSError):
                    if ref.relpath != "references/principles.md" or ref.path.resolve() != PRINCIPLES_PATH.resolve():
                        continue
                raw = ref.path.read_bytes()
                if len(raw) <= 50 * 1024 * 1024:
                    zf.writestr(prefix + ref.relpath.replace("\\", "/"), raw)
            if PRINCIPLES_PATH.is_file() and not any(ref.relpath == "references/principles.md" for ref in skill.references):
                zf.writestr(prefix + "references/principles.md", PRINCIPLES_PATH.read_bytes())
            license_path = Path(__file__).resolve().parent.parent / "LICENSE"
            if license_path.is_file():
                zf.writestr(prefix + "LICENSE", license_path.read_bytes())
    data = buf.getvalue()
    if len(data) > 50 * 1024 * 1024 and not force:
        raise ValueError("ZIP exceeds 50 MB")
    return data, reports
