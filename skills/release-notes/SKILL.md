---
name: release-notes
description: Turns commits, pull requests, or change lists into concise release notes for users. Use when preparing a release announcement or changelog entry from supplied changes. Do not use when the user wants commit messages or has not supplied change records.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - releases
    - documentation
    - writing
---

# Purpose
Turn developer-facing change records (commit logs, pull-request titles, or change notes) into release notes that help users decide within seconds whether an update matters to them.

# Input
- Commit logs, a pull-request list, or change notes (any format).
- The version number. If unknown, write `vX.Y.Z` and ask for it.
- The intended audience (default: end users; follow an explicit request for a technical audience).

# Procedure
1. Classify changes as **Added / Changed / Fixed / Deprecated (breaking changes)**.
2. Combine changes with no user impact (internal refactors, tests, CI) into a single line under “Other”.
3. Rewrite each item as one sentence from the user's perspective. Usually omit function names and file paths.
4. For every breaking change, include **migration steps**, with before-and-after code examples.
5. Sort items from highest to lowest user impact.
6. Start with highlights in no more than three lines.

# Output format
Include every heading below. If a category has no items, write “None”.

```markdown
# Release notes

Version: <version> — Date: <date>

## Highlights
- (The most important changes, in no more than three lines)

## Added
- ...

## Changed
- ...

## Fixed
- ...

## Breaking changes
- **Change**
  - Impact:
  - Migration: `old code` → `new code`

## Other
- Internal improvements, documentation updates, and similar items
```

# Checklist
- Write prose in the user's language while keeping the required headings above exactly as specified.
- Do not paste commit messages verbatim; explain changes from the user's perspective.
- Apply the shared principles in `references/principles.md`.
- Search for `BREAKING`, `!`, `removed`, and `deprecated` to avoid missing breaking changes.
- Do not use emoji.
