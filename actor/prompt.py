"""スキル群を Gemini の ``system_instruction`` へ組み立てる。

Gemini の Skills は「1つの会話に複数のスキルを重ねられる」のが特徴なので、
Actor も複数スキルを決定的な順序で 1 つの system_instruction に合成する。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .skills import DEFAULT_MEDIA_LIMIT, DEFAULT_INLINE_LIMIT, Skill

CATALOG_HEADER = """## 利用できるスキル（カタログ）
あなたは下記のスキルを追加で使える。今回の依頼に合うものがあれば、
「そのスキルの手順に従った」と一言添えたうえで、その手順に沿って回答してよい。

{catalog}
"""


def catalog_block(skills: Iterable[Skill], exclude: Sequence[str] = ()) -> str:
    excluded = set(exclude)
    lines = []
    for skill in sorted(skills, key=lambda s: s.name):
        if skill.name in excluded:
            continue
        desc = skill.description or "(説明なし)"
        lines.append(f"- `{skill.name}`: {desc}")
    if not lines:
        return ""
    return CATALOG_HEADER.format(catalog="\n".join(lines))


def compose_system_text(
    skills: Sequence[Skill],
    *,
    preamble: str,
    all_skills: Sequence[Skill] | None = None,
    with_catalog: bool = False,
    inline_limit: int = DEFAULT_INLINE_LIMIT,
) -> str:
    """system_instruction のテキスト部分を組み立てる。"""
    blocks: list[str] = []
    if preamble.strip():
        blocks.append(preamble.strip())

    if skills:
        names = ", ".join(s.name for s in skills)
        blocks.append(f"## 適用中のスキル: {names}")
        for skill in skills:
            blocks.append(skill.instruction_block(inline_limit=inline_limit))
    else:
        blocks.append("## 適用中のスキル: なし\n（スキルを指定する場合は、ユーザーに `/スキル名` の入力を促してください）")

    if with_catalog and all_skills:
        catalog = catalog_block(all_skills, exclude=[s.name for s in skills])
        if catalog:
            blocks.append(catalog)

    blocks.append(
        "## 出力の作法\n"
        "- スキルに指定された出力形式があれば必ずそれに従う。\n"
        "- スキルの手順を省略した場合は、省略した事実を明示する。\n"
        "- 不明な点は推測で埋めず、ユーザーに質問する。"
    )
    return "\n\n".join(blocks)


def build_system_instruction(
    skills: Sequence[Skill],
    *,
    preamble: str,
    all_skills: Sequence[Skill] | None = None,
    with_catalog: bool = False,
    inline_limit: int = DEFAULT_INLINE_LIMIT,
    media_limit: int = DEFAULT_MEDIA_LIMIT,
    include_media: bool = True,
) -> dict[str, Any]:
    """Gemini API にそのまま渡せる ``systemInstruction`` オブジェクトを作る。"""
    text = compose_system_text(
        skills,
        preamble=preamble,
        all_skills=all_skills,
        with_catalog=with_catalog,
        inline_limit=inline_limit,
    )
    parts: list[dict[str, Any]] = [{"text": text}]
    if include_media:
        for skill in skills:
            parts.extend(skill.media_parts(total_limit=media_limit))
    return {"parts": parts}


def system_text_of(system_instruction: dict[str, Any]) -> str:
    """systemInstruction からテキスト部分だけを取り出す。"""
    return "\n".join(p.get("text", "") for p in system_instruction.get("parts", []) if "text" in p)


def estimate_tokens(text: str) -> int:
    """トークン数のざっくり見積り（精度より安定性と速度を優先）。

    日本語は 1 文字 ≒ 0.6〜1 トークン、ASCII は 4 文字 ≒ 1 トークンとして概算する。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3040" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef")
    ascii_count = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - cjk - ascii_count
    return int(cjk * 0.9 + ascii_count / 4 + other * 0.8) + 1
