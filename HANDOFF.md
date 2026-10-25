# Actor-si handoff

Last reviewed: 2026-10-25

Repository: <https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si>

This file describes the maintained implementation and operational checks. The Japanese documentation lives under [`docs/ja/`](docs/ja/), and the Japanese README is [`README.ja.md`](README.ja.md). The historical snapshot [`docs/development-handoff.md`](docs/development-handoff.md) is not current guidance.

## Project overview

Actor-si is a local-first CLI, HTTP API, web UI, and resident bot that loads `SKILL.md` instructions and calls the Gemini API. Its core uses only the Python standard library; the Google SDK backend is optional. Without provider credentials, the built-in simulator supports offline checks.

- CLI and packaging: `actor/cli.py`, `pyproject.toml`
- HTTP API and browser UI: `actor/server.py`, `actor/web/`
- Chat and providers: `actor/chat.py`, `actor/providers.py`
- Skill loading and composition: `actor/skills.py`, `actor/prompt.py`
- Portable ZIP lint and export: `actor/portable.py`
- Output-format contract tests: `actor/skill_tests.py`
- English default skills: `skills/` (brand-voice, code-review, minutes-writer, release-notes, skill-author)
- Japanese skills: `i18n/ja/skills/` (opt in with `--skills-dir i18n/ja/skills`)

See [`README.md`](README.md) for setup, [`docs/bot.md`](docs/bot.md) for resident bots, [`docs/keys.md`](docs/keys.md) for provider credentials and quota rotation, [`docs/skills-compat.md`](docs/skills-compat.md) for portability, and [`docs/agensi.md`](docs/agensi.md) for portable export.

## Development and validation

Run from the repository root:

```sh
# Standard-library unit suite
python -m unittest discover -s tests -q

# Check output-format assertions for the default English skills
python -m actor skills test

# Lint and export portable skills
python -m actor skills lint --target agensi
python -m actor skills export minutes-writer --target agensi -o /tmp/minutes-writer.zip

# Check the Japanese skills explicitly
python -m actor skills --skills-dir i18n/ja/skills lint --target agensi
python -m actor skills --skills-dir i18n/ja/skills test

# Run the local server and diagnostics
python -m actor doctor
python -m actor serve --port 8080
```

The format-only skill tests inspect templates and assertions; they do not call a model. `--live` sends prompts to the configured provider and may incur charges. The optional UI smoke test uses Node and `jsdom`; its token-protected CI job uses a randomly generated, ephemeral token.

## Security behavior

- API `POST`, `PUT`, and `DELETE` mutations require `Content-Type: application/json`.
- Mutating browser requests accept same-origin requests or origins explicitly listed in `ACTOR_CORS_ORIGINS`. Requests without an Origin header are permitted for non-browser clients, subject to JSON requirements and any configured token.
- Tokens are accepted through `X-Actor-Token`, `X-Room-Token`, or `Authorization: Bearer`. Query-string tokens are not accepted.
- Content Security Policy restricts `frame-ancestors` to `'self'`. Set a room token before exposing the server beyond loopback, and configure host/origin allowlists for the deployment.
- Do not put credentials, private account data, or personal email addresses in source, logs, fixtures, or documentation. Keep runtime data in ignored `.actor/` files and local secret stores.

## Portable Agent Skills

- Export produces one skill per ZIP with `<name>/SKILL.md` at the package root.
- ZIPs are reproducible for identical inputs. HTML comments are removed from prose but preserved inside fenced and indented code examples.
- Shared `principles.md` and the MIT `LICENSE` are required in source and in the packaged Python resources; tests verify the copies stay synchronized.
- Lint checks the exact archive variant selected for export, including the `--keep-allowed-tools` option. Use the same option for lint and export.
- Lint is a conservative approximation, not a safety guarantee or marketplace approval. Review the complete ZIP and any warnings manually.
- Binary references, hidden files, caches, tests, and dependencies are excluded. Actor does not submit skills or operate external accounts.

## Skill output-format checks

Each `skills/<name>/tests/cases.yaml` is JSON syntax compatible with YAML 1.2. The checker discovers the output-format section in either supported language. Angle-bracket text such as `<Meeting name>` in assertions matches a non-empty placeholder value.

- `python -m actor skills test` checks the default English skill contracts.
- `python -m actor skills --skills-dir i18n/ja/skills test` checks the Japanese copies.
- When a template changes, update that locale's case file in the same change. Keep both sets of tests meaningful; do not weaken assertions to make a check pass.

## Release and change constraints

- The security fixes from PR #5 are merged. The next prepared version is `v0.2.1`; its release notes are in [`docs/release-notes-v0.2.1.md`](docs/release-notes-v0.2.1.md). Check that the changes are on `main` and that the version tag/release does not already exist before publishing.
- Preserve the published `v0.2.0` tag and release. Do not rewrite published history or force-push.
- Do not modify `examples/mika` unless explicitly asked.
- `LISTING.md` has not been verified against live third-party hosts; do not claim host compatibility until tested.
- GitHub repository About/Topics could not be changed due to an integration permission error; an authorized owner must update them manually if desired.
