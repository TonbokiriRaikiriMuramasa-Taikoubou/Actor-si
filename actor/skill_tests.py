"""Small deterministic skill contract tests; cases.yaml is JSON, valid YAML 1.2."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .chat import ChatEngine
from .sessions import Session
from .skills import SkillLibrary

def run_cases(library: SkillLibrary, names: list[str] | None = None, *, live: bool = False, settings: Any = None) -> dict[str, Any]:
    selected = set(names or [])
    cases = []
    for skill in library.all():
        if selected and skill.name not in selected:
            continue
        cases_path = skill.root / "tests" / "cases.yaml"
        if cases_path.is_file():
            parsed = json.loads(cases_path.read_text(encoding="utf-8"))
            cases.extend(dict(case, skill=skill.name) for case in parsed)
    if not cases:
        raise ValueError("matching skills/<name>/tests/cases.yaml files not found")
    engine = ChatEngine(settings) if live else None
    results = []
    for case in cases:
        skill = library.get(case["skill"])
        if skill is None:
            results.append({"skill": case["skill"], "passed": False, "error": "skill not found"})
            continue
        if live:
            if engine is None or not settings.has_api_key:
                raise ValueError("--live requires a configured API key; live calls may incur charges")
            session = Session(id="skill-test", title="skill test", model=settings.model)
            pieces = []
            final = {}
            for event in engine.turn(session, case["input"], explicit_skills=[skill.name], stream=False, persist=False):
                if event.get("type") == "text":
                    pieces.append(str(event.get("text", "")))
                elif event.get("type") == "done":
                    final = event
            output = "".join(pieces)
            simulated = bool(final.get("simulated"))
        else:
            output = str(case.get("simulation_output", ""))
            simulated = True
        missing = [expected for expected in case.get("assert_contains", []) if expected not in output]
        if live and simulated:
            missing.append("live request used simulator instead of API")
        results.append({"skill": skill.name, "case": case["name"], "passed": not missing,
                        "missing": missing, "simulated": simulated, "output": output})
    return {"mode": "live" if live else "simulator", "passed": all(x["passed"] for x in results), "cases": results}
