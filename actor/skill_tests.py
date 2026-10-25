"""Skill output-contract tests. Format-only mode inspects specs; it does not fake model output."""
from __future__ import annotations

import json
import re
from typing import Any

from .chat import ChatEngine
from .sessions import Session
from .skills import SkillLibrary, fenced_blocks


OUTPUT_FORMAT_HEADINGS = {"# Output format", "# 出力フォーマット"}


def _output_format_spec(skill: Any) -> str:
    """Return the body of the English or Japanese output-format section."""
    lines = skill.instructions.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() in OUTPUT_FORMAT_HEADINGS), None)
    if start is None:
        return ""
    result: list[str] = []
    fence_char = ""
    fence_size = 0
    for line in lines[start + 1:]:
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if not fence_char:
            if marker:
                fence_char = marker.group(1)[0]
                fence_size = len(marker.group(1))
            elif re.match(r"^#\s+", line):
                break
        elif marker and marker.group(1)[0] == fence_char and len(marker.group(1)) >= fence_size and not line[marker.end():].strip():
            fence_char = ""
            fence_size = 0
        result.append(line)
    section = "\n".join(result)
    parts = [section]
    referenced = set(re.findall(r"references/[A-Za-z0-9_./-]+", section))
    for ref in skill.references:
        if ref.relpath in referenced and ref.kind == "text" and not ref.error:
            parts.append(ref.text)
    blocks = []
    for part in parts:
        blocks.extend(content for _, content in fenced_blocks(part))
    return "\n".join(blocks)


def _spec_headings(spec: str) -> list[str]:
    headings: list[str] = []
    for line in spec.splitlines():
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = match.group(2).strip()
        if re.search(r"(?i)(任意|必要な場合のみ|optional|only when needed|if needed)", title):
            continue
        heading = f"{match.group(1)} {title}"
        if heading not in headings:
            headings.append(heading)
    return headings


def _assertion_matches(output: str, assertion: str) -> bool:
    """Treat angle-bracket values in template assertions as non-empty placeholders."""
    pieces: list[str] = []
    cursor = 0
    for placeholder in re.finditer(r"<[^<>]+>", assertion):
        pieces.append(re.escape(assertion[cursor:placeholder.start()]))
        pieces.append(r"[^\r\n]+?")
        cursor = placeholder.end()
    pieces.append(re.escape(assertion[cursor:]))
    return re.search("".join(pieces), output) is not None


def _load_cases(skill: Any) -> list[dict[str, Any]]:
    cases_path = skill.root / "tests" / "cases.yaml"
    if not cases_path.is_file():
        return []
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or any(not isinstance(case, dict) for case in cases):
        raise ValueError(f"{cases_path}: expected a JSON/YAML list of case objects")
    return cases


def run_cases(library: SkillLibrary, names: list[str] | None = None, *, live: bool = False,
              settings: Any = None) -> dict[str, Any]:
    selected = set(names or [])
    cases: list[dict[str, Any]] = []
    for skill in library.all():
        if selected and skill.name not in selected:
            continue
        cases.extend(dict(case, skill=skill.name) for case in _load_cases(skill))
    if not cases:
        raise ValueError("matching skills/<name>/tests/cases.yaml files not found")

    engine = ChatEngine(settings) if live else None
    results = []
    for case in cases:
        skill = library.get(case["skill"])
        if skill is None:
            results.append({"skill": case["skill"], "passed": False, "error": "skill not found"})
            continue
        expected = list(case.get("assert_contains", []))
        spec = _output_format_spec(skill)
        specified_headings = _spec_headings(spec)
        missing_from_assertions = [heading for heading in specified_headings if heading not in expected]
        stale_assertions = [heading for heading in expected if heading not in spec]

        if live:
            if engine is None or settings is None or not settings.has_api_key:
                raise ValueError("--live requires a configured API key; live calls may incur charges")
            session = Session(id="skill-test", title="skill test", model=settings.model)
            pieces: list[str] = []
            final: dict[str, Any] = {}
            for event in engine.turn(session, case["input"], explicit_skills=[skill.name], stream=False, persist=False):
                if event.get("type") == "text":
                    pieces.append(str(event.get("text", "")))
                elif event.get("type") == "done":
                    final = event
            output = "".join(pieces)
            missing_from_output = [expected_text for expected_text in expected if not _assertion_matches(output, expected_text)]
            used_simulator = bool(final.get("simulated"))
            if used_simulator:
                missing_from_output.append("configured provider returned simulator output instead of a live response")
            passed = bool(specified_headings) and not missing_from_assertions and not stale_assertions and not missing_from_output
            results.append({
                "skill": skill.name, "case": case["name"], "check_type": "model-output",
                "passed": passed, "spec_headings": specified_headings,
                "missing_from_assertions": missing_from_assertions, "stale_assertions": stale_assertions,
                "missing_from_output": missing_from_output, "simulated": used_simulator, "output": output,
            })
        else:
            passed = bool(specified_headings) and not missing_from_assertions and not stale_assertions
            results.append({
                "skill": skill.name, "case": case["name"], "check_type": "format-only",
                "passed": passed, "message": "Format-only check; no model output was generated.",
                "spec_headings": specified_headings,
                "missing_from_assertions": missing_from_assertions, "stale_assertions": stale_assertions,
                "output": None,
            })
    return {"mode": "live" if live else "format-only",
            "passed": all(case["passed"] for case in results), "cases": results}
