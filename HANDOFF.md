# Actor-si maintainer handoff

Last reviewed: 2026-10-26

Repository: <https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si>

Use this file for maintained project guidance. The historical snapshot [`docs/development-handoff.md`](docs/development-handoff.md) is for context only; its test counts and implementation notes are not current.

## Language editions

English is the default source edition. Japanese documentation and skills are maintained separately:

| Content | English (default) | Japanese |
|---|---|---|
| User guide | [`README.md`](README.md) | [`README.ja.md`](README.ja.md) |
| Documentation | [`docs/`](docs/) | [`docs/ja/`](docs/ja/) |
| Skills | [`skills/`](skills/) | [`i18n/ja/skills/`](i18n/ja/skills/) |

Japanese skills are opt-in with `--skills-dir i18n/ja/skills`. Keep the procedure and checklist order aligned between locales, and update each locale's output-format cases when its template changes. Skill headings are fixed by that edition's output contract; write the response body in the user's language.

The CLI and web interface chrome, built-in tool descriptions, and application status/error messages are in English. This does not translate skill instructions, user-provided content, or model responses; those follow the selected skill and the user's language.

## Project and code map

Actor-si is the product name; `actor` is the CLI command. The project is a local-first CLI, HTTP API, web UI, and resident bot that loads `SKILL.md` instructions and can call the Gemini API. The core uses only Python's standard library. The Google SDK backend is optional; the built-in simulator supports offline development.

- CLI and packaging: `actor/cli.py`, `pyproject.toml`
- HTTP API and browser UI: `actor/server.py`, `actor/web/`
- Chat and provider backends: `actor/chat.py`, `actor/providers.py`, `actor/genai_provider.py`
- Key management and tools: `actor/keys.py`, `actor/tools.py`
- Skills, prompt composition, and output-format tests: `actor/skills.py`, `actor/prompt.py`, `actor/skill_tests.py`
- Resident rooms and bot: `actor/rooms.py`, `actor/bot.py`, `actor/sessions.py`
- Portable ZIP lint and export: `actor/portable.py`
- Shared English principles: `skills/_house/principles.md`
- Shared Japanese principles: `i18n/ja/skills/_house/principles.md`

For task-specific details, see [`docs/bot.md`](docs/bot.md), [`docs/keys.md`](docs/keys.md), [`docs/skills-compat.md`](docs/skills-compat.md), and [`docs/agensi.md`](docs/agensi.md).

## Development checks

Run from the repository root:

```sh
# Syntax check and full standard-library test suite
python -m compileall -q actor tests
python -m unittest discover -s tests -q

# English default skills
python -m actor skills lint --target agensi
python -m actor skills test
python -m actor skills export minutes-writer --target agensi -o /tmp/minutes-writer.zip

# Japanese skills; pass the locale directory explicitly
python -m actor skills --skills-dir i18n/ja/skills lint --target agensi
python -m actor skills --skills-dir i18n/ja/skills test
python -m actor skills --skills-dir i18n/ja/skills export minutes-writer --target agensi -o /tmp/minutes-writer-ja.zip

# Environment diagnostics
python -m actor doctor
```

The default skill tests are format-only: they inspect output templates and assertions without calling a model. `--live` sends prompts to the configured provider and may incur charges. The optional UI smoke test requires Node and `jsdom`; its CI job uses a newly generated temporary room token. Do not check fixed tokens or provider credentials into source, test fixtures, logs, or documentation.

## Security and operations

- API `POST`, `PUT`, and `DELETE` mutations require `Content-Type: application/json`.
- Mutating browser requests accept same-origin requests or origins explicitly listed in `ACTOR_CORS_ORIGINS`. Requests without an `Origin` header are permitted for non-browser clients, subject to JSON requirements and any configured token.
- Room tokens are accepted through `X-Actor-Token`, `X-Room-Token`, or `Authorization: Bearer`; query-string tokens are not accepted.
- Content Security Policy restricts `frame-ancestors` to `'self'`. Set a room token before exposing the server beyond loopback, and configure host/origin allowlists for the deployment.
- Keep runtime data in ignored `.actor/` files or a local secret store. Do not commit credentials, private account data, or personal email addresses.
- Use GitHub's noreply address for authored commits. The account's “Keep my email addresses private” and email-exposure push protection settings are enabled; already-published commit attribution remains public and must not be rewritten.

## Portable skill packages

- Export one skill per ZIP with `<name>/SKILL.md` at the package root.
- ZIPs are reproducible for identical inputs. HTML comments are removed from prose but preserved inside fenced and indented code examples.
- `principles.md` and the MIT `LICENSE` are required in the source and packaged resources; tests verify the canonical and packaged copies stay synchronized.
- Lint checks the same archive variant selected for export. If using `--keep-allowed-tools`, pass it to both lint and export.
- Lint is a conservative approximation, not a safety guarantee or marketplace approval. Review the complete ZIP and warnings manually. Japanese privacy advice should not trigger concealment findings; explicit concealment from the user and concealment paired with sending, uploading, or execution should remain detectable.
- Binary references, hidden files, caches, tests, and dependencies are excluded. A root source `LICENSE` is replaced by the canonical bundled MIT license, so it is not an omitted-reference warning. Actor does not submit skills or operate external accounts.
- English bundled skill descriptions state both `Use when` and `Do not use when`; keep that boundary in the description of new English skills. Japanese skill files are maintained separately.

## Release and change constraints

- PRs #5–#10 are merged: security hardening; English source and opt-in Japanese skills; English CLI/UI; lint, packaging, auth-header and positioning follow-ups; finalized v0.2.1 notes; and refined Japanese lint plus Actor-si branding.
- The annotated `v0.2.1` tag and GitHub Release are published from `main`; release notes are in [`docs/release-notes-v0.2.1.md`](docs/release-notes-v0.2.1.md). v0.3.0 release notes are maintained in English and Japanese at [`docs/release-notes-v0.3.0.md`](docs/release-notes-v0.3.0.md) and [`docs/ja/release-notes-v0.3.0.md`](docs/ja/release-notes-v0.3.0.md).
- Keep `pyproject.toml` and `actor/__init__.py` version values synchronized. Before future release tags, confirm reviewed changes are on `main`, the version is correct, and the tag/release does not already exist; publishing requires explicit maintainer approval.
- Preserve the published `v0.2.0` tag and Release, including its body. Do not rewrite published history or force-push.
- Do not modify `examples/mika` unless explicitly asked.
- Keep Sponsors links in `FUNDING.md`; do not add them to `LISTING.md` or skill ZIPs.
- Do not submit packages to third-party services or change GitHub settings without explicit authorization.
- [`LISTING.md`](LISTING.md) is a draft and has not been verified against live third-party hosts. Do not claim host compatibility until tested.
- Repository About/Topics have been configured manually. The current GitHub integration cannot write repository metadata; use GitHub settings or reconnect with appropriate permissions for future changes.
