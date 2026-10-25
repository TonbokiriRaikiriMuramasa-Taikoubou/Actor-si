# Development handoff archive — Actor (2026-10-13)

> Historical snapshot retained for context. It describes an earlier state and is not current security guidance or a current test-count record. See the root [`HANDOFF.md`](../HANDOFF.md), [`README.md`](../README.md), [`SECURITY.md`](../SECURITY.md), and current guides for the maintained instructions.

## Project summary at the time

Actor was a local-first Gemini client that loaded `SKILL.md` as a system instruction. It exposed a CLI, HTTP API, web UI, and resident bot, with a standard-library core and an optional `google-genai` SDK backend. A simulator supported offline development without provider credentials.

## Features recorded in this snapshot

- Skill loading, composition, deterministic selection, and ZIP export.
- CLI chat, one-shot and JSON modes, HTTP chat and SSE, and a browser UI.
- Resident bots with room polling, local sessions, and restart cursors.
- Image and PDF attachments, bounded function-calling tools, and optional webhooks.
- Multiple credential sources, masked key display, and automatic key/model fallback after rate limits.
- Unit tests and optional browser smoke tests.

The test counts, branch name, command output, and implementation details in the original snapshot were specific to October 13, 2026, and must not be used as current values.

## Historical design notes

- Sessions, rooms, bot cursors, and local key state lived under `.actor/` and were ignored by Git.
- Tools were read-only by default; file writes and network requests required explicit opt-in.
- Room history used append-only JSON and sequence-based polling.
- Key state stored fingerprints and usage counters rather than raw credential values.

## Current references

- [`HANDOFF.md`](../HANDOFF.md) — maintained project state, supported commands, and release notes.
- [`README.md`](../README.md) — English quickstart and user guide.
- [`README.ja.md`](../README.ja.md) — Japanese user guide.
- [`docs/bot.md`](bot.md) — current resident-bot operations.
- [`docs/keys.md`](keys.md) — current credential and quota behavior.
- [`docs/skills-compat.md`](skills-compat.md) — portable skill constraints.
