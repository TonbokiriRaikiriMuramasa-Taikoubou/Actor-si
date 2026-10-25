---
name: code-review
description: Reviews code changes for actionable defects and security risks, then reports findings by severity. Use when the user asks for a code, diff, or pull-request review. Do not use when the user wants implementation or a general explanation without changes to inspect.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - code-review
    - quality
---

# Purpose
Review a diff, code fragment, or pull request and report **only actionable findings that could cause bugs**, ranked by severity. Do not add stylistic preferences or rewording suggestions. This skill is read-only; do not change files.

# Input
- The code or diff to review.
- The language, runtime, and project conventions (if unknown, use general principles).

# Procedure
1. Work through `references/checklist.md` from top to bottom and inspect relevant areas.
2. Assign a **severity** to each finding (Critical / High / Medium / Low).
   - Critical: directly causes data loss, authorization bypass, or production outage.
   - High: causes a bug, exception, or information disclosure under specific conditions.
   - Medium: has a clear negative impact on maintainability or performance.
   - Low: minor, but still a potential bug; do not report preferences.
3. Include the **smallest viable fix** (a code snippet) for each finding.
4. For findings that need testing, provide a verification procedure and sample input.
5. If there are no findings, write only “LGTM”; do not invent an issue.

# Output format
```markdown
## Conclusion
N finding(s) (Critical: x / High: y / Medium: z / Low: w)

## Findings
### [High] <One-line summary>
- Location: <file:line or function>
- Problem: <why it is a bug and the specific trigger>
- Fix:
```language
// corrected code
```
- Verification: <reproduction steps or test input>

## What looks good (optional; 1–2 lines)
```

# Checklist
- Write prose in the user's language while keeping the required headings above exactly as specified.
- Apply the shared principles in `references/principles.md`.
- If information is missing, say “Not verified because it is not included in the diff.”
- Do not make security findings without stating concrete reproduction conditions.
- Do not report preferences about naming, indentation, or comment volume.
- At the end, verify that the severity counts add up to the number of findings.
