---
name: brand-voice
description: Revises user-provided copy to match a calm, consistent brand voice while preserving meaning and facts. Use when polishing announcements, emails, release notes, or other external-facing text.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - writing
    - consistency
    - tone
---

# Purpose
Polish external-facing copy (announcements, emails, release notes, and landing-page text) to match the brand voice and style rules. **Change the wording, not the meaning.**

# Scope
Treat the request as a standalone editing task. Preserve the source text's meaning and facts while improving its final wording.

# Procedure
1. Identify the text's purpose (announcement, request, explanation, or apology).
2. Make the tone consistent: use the requested register; state confirmed facts directly; avoid excessive formality or honorifics.
3. Apply the style rules below.
4. Keep sentences concise (aim for 60 characters or fewer when editing Japanese). Limit paragraphs to three sentences.
5. Remove hype (such as “revolutionary,” “blazing-fast,” or “guaranteed”) and unnecessary exclamation marks.
6. Do not alter facts. Show only the edited text and the changes made.

# Style rules
- Use half-width Latin letters and digits. Put a space between numbers and units (for example, `10 GB` and `3 min`).
- Use punctuation and parentheses appropriate to the text's language. For Japanese, follow standard Japanese punctuation and bracket conventions, and use half-width parentheses around Latin text.
- Use the product's official name on first mention; a shortened form is acceptable afterward.
- For Japanese, prefer the standard kana spellings for common words over outdated kanji spellings.
- Do not end headings with periods; prefer concise noun phrases.

# Output format
```markdown
## Polished copy
<Final copy in the user's language>

## Changes (up to 7 items)
| # | Before | After | Reason |
| --- | --- | --- | --- |
```

# Checklist
- Write prose in the user's language while keeping the required headings above exactly as specified.
- Apply the shared principles in `references/principles.md`.
- Do not alter facts, figures, proper names, or links. If a change seems necessary, flag it without making it.
- Do not rewrite passages whose intent is unclear; mark them **Needs confirmation**.
- Do not pad sentences merely to make them sound more polite.
