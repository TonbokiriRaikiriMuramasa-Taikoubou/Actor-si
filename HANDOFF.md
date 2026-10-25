# Actor-si maintainer handoff

Last reviewed: 2026-10-25

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

Actor-si is a local-first CLI, HTTP API, web UI, and resident bot that loads `SKILL.md` instructions and can call the Gemini API. The core uses only Python's standard library. The Google SDK backend is optional; the built-in simulator supports offline development.

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

## Portable skill packages

- Export one skill per ZIP with `<name>/SKILL.md` at the package root.
- ZIPs are reproducible for identical inputs. HTML comments are removed from prose but preserved inside fenced and indented code examples.
- `principles.md` and the MIT `LICENSE` are required in the source and packaged resources; tests verify the canonical and packaged copies stay synchronized.
- Lint checks the same archive variant selected for export. If using `--keep-allowed-tools`, pass it to both lint and export.
- Lint is a conservative approximation, not a safety guarantee or marketplace approval. Review the complete ZIP and warnings manually.
- Binary references, hidden files, caches, tests, and dependencies are excluded. Actor does not submit skills or operate external accounts.

## Release and change constraints

- The security fixes from PR #5 are merged. PR #6 adds the English source edition and opt-in Japanese localization; it does not change the release version.
- The next prepared version is `v0.2.1`; its release notes are in [`docs/release-notes-v0.2.1.md`](docs/release-notes-v0.2.1.md). Before publishing, confirm the changes are on `main` and the tag/release does not already exist.
- Preserve the published `v0.2.0` tag and release. Do not rewrite published history or force-push.
- Do not modify `examples/mika` unless explicitly asked.
- [`LISTING.md`](LISTING.md) is a draft and has not been verified against live third-party hosts. Do not claim host compatibility until tested.
- Repository About/Topics could not be changed due to an integration permission error; an authorized owner would need to update them manually.
