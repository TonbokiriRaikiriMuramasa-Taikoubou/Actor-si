---
name: minutes-writer
description: Turns meeting transcripts or notes into structured minutes with decisions, action items, owners, due dates, and open questions. Use when the user provides meeting material and asks for minutes.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - meeting-minutes
    - meetings
    - extraction
---

# Purpose
Turn raw meeting records (transcripts, notes, or chat histories) into minutes that let later readers quickly understand the decisions.

# Input
- A meeting transcript or bullet-point notes.
- The meeting name, date and time, and participants. Write “Unknown” when a detail is unavailable.
- Any conventions for the destination. Unless specified, follow `references/format.md`.

# Procedure
1. Read the full record and split it into topic-based sections. Omit small talk, acknowledgements, and tangents.
2. Extract **decisions**. Include only statements that settle a choice, such as “we will proceed with…” or “we will not…”.
3. Extract **action items**. Include an owner, due date, and deliverable for each. Mark unknown details as `Needs confirmation`; do not leave them blank.
4. List **open questions**: unresolved issues that need a decision before the next meeting.
5. For ambiguous intent, do not fill in gaps; add a note such as “(Interpretation: …)”.
6. Fill in the template in `references/format.md` and perform a final self-review.

# Output format
Follow the template in `references/format.md` exactly. Do not change the heading order or table columns.

# Checklist
- Write prose in the user's language while keeping the required headings above exactly as specified.
- Keep decisions separate from action items: “what was decided” and “what needs to be done” belong in different sections.
- Apply the shared principles in `references/principles.md`.
- If a participant's name is unknown, write “Unknown”; do not infer a role or invent a placeholder name.
- End the output with a one-line count of items marked “Needs confirmation”.
