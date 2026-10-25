"""Engine that selects skills, composes system instructions, and calls Gemini."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from .config import Settings
from .prompt import build_system_instruction, estimate_tokens, system_text_of
from .providers import Provider, ProviderError, get_provider
from .sessions import Session, SessionStore
from .skills import Skill, SkillLibrary, resolve_overrides
from .tools import ToolRegistry, build_registry, parse_args

__all__ = ["Selection", "ChatEngine", "SkillDraft"]


@dataclass
class Selection:
    """1ターン分のスキル選択結果。"""

    skills: list[Skill] = field(default_factory=list)
    requested: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    auto_scores: list[dict[str, Any]] = field(default_factory=list)
    overrides: dict[str, Any] = field(default_factory=dict)
    override_sources: dict[str, str] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    system_instruction: dict[str, Any] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.skills]

    def to_dict(self, include_prompt: bool = False) -> dict[str, Any]:
        data = {
            "skills": [s.to_dict(include_instructions=False) for s in self.skills],
            "names": self.names,
            "requested": self.requested,
            "missing": self.missing,
            "auto_scores": self.auto_scores,
            "overrides": dict(self.overrides),
            "override_sources": dict(self.override_sources),
            "effective_temperature": self.overrides.get("temperature"),
            "effective_model": self.overrides.get("model", ""),
            "tools": self.tools,
            "tool_names": self.tool_names,
            "system_chars": len(system_text_of(self.system_instruction)),
            "system_tokens": estimate_tokens(system_text_of(self.system_instruction)),
        }
        if include_prompt:
            data["system_instruction"] = system_text_of(self.system_instruction)
        return data


@dataclass
class SkillDraft:
    name: str
    description: str
    instructions: str
    source: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "instructions": self.instructions, "source": self.source}

    def to_markdown(self) -> str:
        return f"---\nname: {self.name}\ndescription: {self.description}\n---\n\n{self.instructions.strip()}\n"


DRAFT_PROMPT = """You design reusable Agent Skills in the SKILL.md format.
Turn the request below into a clear, repeatable skill.

# Request
{request}

{context}

# Output rules
- Return only this JSON object. Do not add explanation or code fences.
{{"name": "kebab-case-name", "description": "Concise description with a Use when sentence", "instructions": "Markdown instructions"}}
- Include these sections in instructions: Purpose, Input, Procedure (numbered steps), Output format, and Checklist.
- Use specific, actionable verbs so different users can follow the same procedure.
- Write the skill in the language used in the request. Keep required output headings explicit and consistent.
- Do not invent requirements or facts that were not provided.
"""


class ChatEngine:
    """Actor の頭脳。CLI と Web から共通で使う。"""

    def __init__(
        self,
        settings: Settings,
        library: SkillLibrary | None = None,
        provider: Provider | None = None,
        store: SessionStore | None = None,
    ):
        self.settings = settings
        self.library = library or SkillLibrary(settings.skills_dirs)
        self.provider = provider or get_provider(settings)
        self.store = store or SessionStore(settings.sessions_dir)
        self.tools: ToolRegistry = build_registry(settings, skill_provider=self.library.all)

    # ------------------------------------------------------------------
    # スキル選択
    # ------------------------------------------------------------------
    def select(
        self,
        query: str,
        explicit: Sequence[str] | None = None,
        auto: bool | None = None,
        *,
        max_skills: int | None = None,
        threshold: float | None = None,
    ) -> Selection:
        auto = self.settings.auto_select if auto is None else auto
        limit = max_skills or self.settings.max_auto_skills
        threshold = self.settings.auto_select_threshold if threshold is None else threshold

        self.library.reload()
        skills, missing, scores = self.library.select(
            query, explicit=explicit, auto=auto, limit=limit, threshold=threshold
        )
        overrides, sources = resolve_overrides(skills)
        registry = self.tools_for(skills)
        selection = Selection(
            skills=skills,
            requested=list(explicit or []),
            missing=missing,
            auto_scores=[{"name": n, "score": s} for n, s in scores],
            overrides=overrides,
            override_sources=sources,
            tools=registry.declarations(),
            tool_names=registry.names(),
        )
        selection.system_instruction = self.build_instruction(selection.skills)
        return selection

    def tools_for(self, skills: Sequence[Skill]) -> ToolRegistry:
        """適用中スキルの allowed-tools を反映したツール集合を返す。

        - どのスキルも宣言していない → 設定で有効な全ツール
        - いずれかが宣言している → その和集合（スキルを重ねた分だけ道具も増える）
        """
        declared: list[str] = []
        for skill in skills:
            declared.extend(skill.allowed_tools)
        if not declared:
            return self.tools
        return self.tools.subset(declared)

    def build_instruction(self, skills: Sequence[Skill]) -> dict[str, Any]:
        return build_system_instruction(
            skills,
            preamble=self.settings.preamble,
            all_skills=self.library.all() if self.settings.auto_select else None,
            with_catalog=self.settings.auto_select,
            inline_limit=self.settings.inline_reference_limit,
        )

    # ------------------------------------------------------------------
    # 1ターン
    # ------------------------------------------------------------------
    def turn(
        self,
        session: Session,
        user_message: str,
        *,
        explicit_skills: Sequence[str] | None = None,
        auto: bool | None = None,
        stream: bool | None = None,
        persist: bool = True,
        attachments: Sequence[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """1ターン分のイベントを逐次返す。

        イベント種別: ``selection`` → ``prompt`` → (``thought`` | ``text``)* → ``done``
        """
        stream = self.settings.stream if stream is None else stream
        explicit = list(explicit_skills) if explicit_skills else list(session.skills)

        selection = self.select(user_message, explicit=explicit, auto=auto)
        yield {"type": "selection", **selection.to_dict()}

        if explicit:
            # 明示指定されたスキルはセッションに固定（ピン留め）され、以降のターンでも適用される
            session.skills = list(explicit)
        session.auto_skills = [s.name for s in selection.skills if s.name not in session.skills]
        session.model = self.settings.model
        stored_attachments = self._prepare_attachments(attachments or [])
        session.add_user(user_message, skills=selection.names, auto_skills=session.auto_skills,
                         attachments=stored_attachments)

        messages: list[dict[str, Any]] = []
        for message in session.messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            files = list(message.attachments or [])
            if files:
                item["images"] = [
                    {"mime_type": f["mime_type"], "data": f["data"]}
                    for f in files if f.get("data")
                ]
            messages.append(item)
        # スキル（frontmatter）が指定したパラメータを優先して反映する
        generation_config = self.settings.generation_config(**selection.overrides)
        model_override = str(selection.overrides.get("model") or "")
        effective_model = model_override or self.provider.model

        yield {
            "type": "prompt",
            "system_instruction": system_text_of(selection.system_instruction),
            "generation_config": generation_config,
            "model": effective_model,
            "overrides": dict(selection.overrides),
            "override_sources": dict(selection.override_sources),
            "prompt_tokens_estimate": estimate_tokens(system_text_of(selection.system_instruction))
            + estimate_tokens(user_message),
        }

        thought_parts: list[str] = []
        text_parts: list[str] = []
        usage: dict[str, Any] = {}
        error: str | None = None
        notices: list[dict[str, Any]] = []
        used_model = ""     # 429 で別モデルへ逃げたときの「実際に使ったモデル」
        registry = self.tools_for(selection.skills)
        tool_events: list[dict[str, Any]] = []

        try:
            for round_index in range(max(1, self.settings.max_tool_rounds)):
                calls: list[dict[str, Any]] = []
                saw_call_event = False
                round_text: list[str] = []
                events = self.provider.stream(
                    messages, selection.system_instruction,
                    generation_config=generation_config, model=model_override or None,
                    tools=registry.declarations() or None,
                ) if stream else _as_events(self.provider.generate(
                    messages, selection.system_instruction,
                    generation_config=generation_config, model=model_override or None,
                    tools=registry.declarations() or None,
                ))
                for event in events:
                    kind = event.get("type")
                    if kind == "thought":
                        thought_parts.append(event.get("text", ""))
                    elif kind == "text":
                        round_text.append(event.get("text", ""))
                    elif kind == "function_call":
                        saw_call_event = True
                        calls.append(event.get("call") or {})
                    elif kind == "done":
                        usage = event.get("usage") or usage
                        if event.get("model"):
                            used_model = str(event["model"])
                        notices.extend(event.get("notices") or [])
                        if not saw_call_event:  # 非ストリーム経路では done に載ってくる
                            calls.extend(event.get("function_calls") or [])
                        continue  # 途中経過の done は流さない（最後にまとめて 1 回出す）
                    elif kind == "notice":
                        notices.append({k: v for k, v in event.items() if k != "type"})
                    elif kind == "error":
                        error = event.get("text") or "An error occurred."
                    yield event
                text_parts.extend(round_text)

                if not calls or error:
                    break
                if round_index == self.settings.max_tool_rounds - 1:
                    yield {"type": "tool_limit", "text": "Maximum tool-call rounds reached."}
                    break

                # モデルの functionCall と、こちらの実行結果を履歴に足して次のラウンドへ
                messages.append({"role": "model", "parts": [
                    {"functionCall": {"name": c.get("name", ""), "args": parse_args(c.get("args"))}}
                    for c in calls
                ]})
                response_parts: list[dict[str, Any]] = []
                for call in calls:
                    name = str(call.get("name") or "")
                    args = parse_args(call.get("args"))
                    event = {"type": "tool_call", "name": name, "args": args}
                    yield event
                    tool_events.append(event)
                    result = registry.execute(name, args)
                    event = {"type": "tool_result", "name": name, "ok": result.ok,
                             "result": result.text[:4000], "args": args}
                    yield event
                    tool_events.append(event)
                    response_parts.append({"functionResponse": {
                        "name": name,
                        "response": {"result": result.text[:8000], "ok": result.ok},
                    }})
                messages.append({"role": "user", "parts": response_parts})
        except ProviderError as exc:
            error = str(exc)
            yield {"type": "error", "text": error, "status": exc.status}
        except Exception as exc:  # pragma: no cover - 想定外
            error = f"{type(exc).__name__}: {exc}"
            yield {"type": "error", "text": error}

        content = "".join(text_parts)
        thoughts = "\n".join(p for p in thought_parts if p.strip())
        final_model = used_model or effective_model
        if self.provider.is_mock:
            final_model = f"{effective_model} (simulation)"
        elif used_model and used_model != effective_model:
            final_model = used_model
        message = session.add_assistant(
            content,
            skills=selection.names,
            auto_skills=session.auto_skills,
            tools=[e["name"] for e in tool_events if e["type"] == "tool_call"],
            thoughts=thoughts,
            usage=usage,
            model=final_model,
            simulated=self.provider.is_mock,
        )
        if session.title in {"", "New chat"}:
            session.title = session.auto_title()
        if persist:
            self.store.save(session)

        yield {
            "type": "done",
            "usage": usage,
            "model": message.model,
            "requested_model": effective_model if message.model != effective_model else "",
            "notices": notices,
            "simulated": self.provider.is_mock,
            "error": error,
            "tool_calls": [e for e in tool_events if e["type"] == "tool_call"],
            "session": session.to_dict(include_thoughts=False),
            "message": message.to_dict(),
        }

    # ------------------------------------------------------------------
    # 添付ファイル
    # ------------------------------------------------------------------
    def _prepare_attachments(self, attachments: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """検証して、セッションに保存できる形（base64 込み）へ整える。"""
        limit = self.settings.max_attachment_bytes
        prepared: list[dict[str, Any]] = []
        for item in attachments[: self.settings.max_attachments]:
            data = item.get("data") or ""
            if isinstance(data, bytes):
                import base64

                data = base64.b64encode(data).decode("ascii")
            if not data:
                continue
            raw_size = int(item.get("size") or 0) or (len(data) * 3) // 4
            if raw_size > limit:
                raise ValueError(
                    f"Attachment '{item.get('name') or 'file'}' is too large "
                    f"({raw_size / 1048576:.1f} MB; limit {limit / 1048576:.0f} MB)"
                )
            prepared.append({
                "name": str(item.get("name") or "file"),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                "size": raw_size,
                "data": data if self.settings.store_attachments else "",
            })
        return prepared

    def complete(
        self,
        session: Session,
        user_message: str,
        *,
        explicit_skills: Sequence[str] | None = None,
        auto: bool | None = None,
        persist: bool = True,
        attachments: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """ストリーミングせずに1ターンを実行して結果を返す。"""
        final: dict[str, Any] = {}
        for event in self.turn(
            session, user_message, explicit_skills=explicit_skills, auto=auto,
            stream=False, persist=persist, attachments=attachments,
        ):
            if event["type"] == "done":
                final = event
        return final

    # ------------------------------------------------------------------
    # 会話からスキルを作る
    # ------------------------------------------------------------------
    def draft_skill(self, request: str, *, context: str = "") -> SkillDraft:
        """自然言語の依頼から SKILL.md のドラフトを作る。"""
        if self.provider.is_mock:
            return _mock_draft(request)

        prompt = DRAFT_PROMPT.format(request=request.strip(), context=context.strip())
        try:
            result = self.provider.generate(
                [{"role": "user", "content": prompt}],
                None,
                generation_config={"temperature": 0.4, "maxOutputTokens": 2048},
            )
        except ProviderError:
            return _mock_draft(request)
        return parse_draft(result.get("text", ""), fallback_request=request)


def _as_events(result: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """非ストリームの結果を、ストリームと同じイベント列に揃える（function calling 対応）。"""
    for notice in result.get("notices") or []:
        yield {"type": "notice", **notice}
    if result.get("thoughts"):
        yield {"type": "thought", "text": result["thoughts"]}
    calls = list(result.get("function_calls") or [])
    for call in calls:
        yield {"type": "function_call", "call": call}
    if result.get("text"):
        yield {"type": "text", "text": result["text"]}
    yield {
        "type": "done",
        "text": "",
        "usage": result.get("usage", {}),
        "model": result.get("model", ""),
        "requested_model": result.get("requested_model", ""),
        "notices": result.get("notices") or [],
        "function_calls": calls,
        "simulated": bool(result.get("raw", {}).get("simulated")),
    }


def parse_draft(text: str, fallback_request: str = "") -> SkillDraft:
    """モデル出力から JSON を拾って SkillDraft にする（壊れていても耐える）。"""
    payload: dict[str, Any] | None = None
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.S)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        draft = _mock_draft(fallback_request)
        draft.instructions = (text or "").strip() or draft.instructions
        draft.source = "model-raw"
        return draft
    from .skills import slugify

    return SkillDraft(
        name=slugify(str(payload.get("name") or fallback_request or "new-skill")),
        description=str(payload.get("description") or "").strip()[:120],
        instructions=str(payload.get("instructions") or "").strip(),
        source="model",
    )


_TEMPLATE = """# Purpose
{request} consistently, using the same quality standards each time.

# Input
- The relevant text, files, or task description.
- Any assumptions, requirements, or constraints.

# Procedure
1. Confirm the goal and expected deliverable.
2. Extract the information needed from the supplied material.
3. Ask about gaps or contradictions instead of guessing.
4. Produce the deliverable using the output format below.
5. Review the result against the request and requirements.

# Output format
## Summary
Provide a concise summary.

## Details
Use bullets and cite supporting material.

## Next steps
List the owner, action, and due date when known.

# Checklist
- Mark uncertain interpretations for confirmation.
- List missing information at the end.
"""


def _mock_draft(request: str) -> SkillDraft:
    from .skills import slugify

    text = (request or "").strip()
    ascii_words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text)
    base = "-".join(w.lower() for w in ascii_words[:3]) or "custom-task"
    description = (text.splitlines()[0] if text else "Skill generated from a request")[:80] or "Skill generated from a request"
    return SkillDraft(
        name=slugify(base),
        description=description,
        instructions=_TEMPLATE.format(request=text.splitlines()[0] if text else "the task"),
        source="template",
    )
