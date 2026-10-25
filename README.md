# Actor-si

[![Tests](https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si/actions/workflows/tests.yml/badge.svg)](https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si/actions/workflows/tests.yml)

Actor-si (the `actor` CLI command) is a local-first workbench for authoring, linting, testing, and packaging portable Agent Skills. Its CLI, HTTP API, and web UI load `SKILL.md` folders as system instructions and run them with Gemini; core features use Python's standard library. Skills can be selected automatically, composed with references, and checked before use. The server binds to loopback by default; external binding requires an API token, and browser cross-origin access requires an explicit allowlist.

## Overview

Actor-si loads folders that follow the open, Markdown-native Agent Skills `SKILL.md` format and can:

- Stack multiple skills into one `system_instruction`.
- Select skills deterministically from a request, with a configurable threshold.
- Include text references, templates, checklists, images, and PDFs with a skill.
- Let the model call tools such as `now`, `read_file`, and `list_files`; a skill can narrow tools with `allowed-tools`.
- Accept image and PDF attachments through the CLI, web UI, or HTTP API.
- Protect rooms and API endpoints with a token and optionally forward room events to a webhook.
- Draft new skills in a conversation and export them as ZIP files.

```text
┌────────────┐   /skills/<name>/SKILL.md   ┌──────────────┐
│ CLI        │──┐                          │ system_      │
│ Web UI     │──┼──▶ Actor-si ──────────────▶│ instruction  │──▶ Gemini API
│ HTTP API   │──┘     select / compose     │ + references │
└────────────┘                             └──────────────┘
```

## What you can use it for

Actor-si is a local workshop for drafting, linting, testing, and packaging portable Agent Skills—not a hosted marketplace or submission service. It also includes five reusable skills for common writing and review tasks. Their descriptions state both `Use when` and `Do not use when` boundaries; use them in Actor-si or export their folders as ZIPs for compatible hosts. Third-party host compatibility has not been independently verified.

### Build and check your own skills

Draft a reusable `SKILL.md` in a conversation with `/skill-author`, then lint the exact portable package locally:

```bash
python3 -m actor skills lint --target agensi
python3 -m actor skills test
```

Lint checks the ZIP Actor-si would export, including folder layout, metadata, supported file types and sizes, likely leaked credentials, and risky concealment or prompt-injection instructions. `skills test` checks required output headings and cases without calling a model; `--live` is an opt-in exception that uses the configured provider.

Export one reproducible ZIP per skill when it is ready:

```bash
python3 -m actor skills export minutes-writer --target agensi -o minutes-writer.zip
```

The ZIP includes the canonical MIT `LICENSE` and shared principles. Review the complete archive, then submit it yourself on any service you choose; Actor-si does not upload, publish, or submit skills.

### Use the five bundled skills

- **`skill-author`** — Drafts a complete, portable `SKILL.md`, including metadata, actionable steps, references, and a testable output format. Use it to create or improve reusable procedures; not for a one-off answer that does not need a skill.
- **`release-notes`** — Turns supplied commits, pull requests, or change lists into user-facing release notes. It keeps the required sections and marks empty ones `None`; use it when preparing notes from actual changes, not to invent changes or write commit messages.
- **`code-review`** — Reviews a supplied diff or pull request for actionable defects and security risks, ranked by severity, with minimal fixes. A clean review returns `LGTM`; use it for review, not implementation or a general code explanation.
- **`minutes-writer`** — Turns transcripts or notes into structured minutes with decisions, actions, owners, deadlines, and open questions. A separate Japanese edition is available in `i18n/ja/skills`; use it for meeting records, not a generic summary.
- **`brand-voice`** — Polishes user-provided copy in a calm, consistent voice while preserving facts and meaning. Use it to revise existing copy, not to fact-check or create unsupported facts.

### What it is not

Actor-si is not a hosted service, and these bundled skills do not directly act on external systems or submit content. They produce or guide text based on material you provide. Actor-si's optional network tool is disabled by default and must be explicitly enabled. If you need a skill that operates an external service, this bundle does not provide that integration.

## 30-second quickstart

```bash
git clone https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si.git
cd Actor-si
python3 -m actor "Create minutes from this meeting transcript"
```

Python 3.9 or newer is sufficient for the standard-library core; installing the package is not required to run it from the repository. Without an API key, the built-in simulator exercises the same skill-selection, composition, streaming, and UI paths without calling Gemini.

### Where to set an API key (four options)

The repository-root `.env` file is the recommended location. You can also save a key through the web UI or `actor key set`; all local key files are ignored by Git.

| Source | Path / setting | Notes |
| --- | --- | --- |
| `.env` | `<repository>/.env` | Supports `GEMINI_API_KEY` or `GEMINI_API_KEYS`. |
| Environment | `GEMINI_API_KEY` / `GEMINI_API_KEYS` | Useful for CI and service managers; multiple values may be comma-, space-, or semicolon-separated. |
| Saved key | `.actor/api_key` | Written by the UI or `actor key set`; file permissions are restricted where supported. |
| Key directory | `.actor/keys/key-*.key` | Written by `actor key add`, one key per file. |

Copy `.env.example` to `.env`, then add your key locally without pasting it into commands, documentation, or source control. The precedence is **`--api-key` > environment > `.env` > `.actor/api_key` > `.actor/keys/`**.

```console
$ python3 -m actor key show
API key: [masked]
Provider: gemini / Model: gemini-3-flash / Available keys: 2 of 2

$ python3 -m actor key list
API keys: 2 (1 available)
  [0] [masked]  active
  [1] [masked]  cooling down

$ python3 -m actor key check
$ python3 -m actor key stats
$ python3 -m actor key rm 1
$ python3 -m actor key clear
```

The server never returns raw keys: `/api/config` and `/api/key` expose only masked values, counts, status, and statistics. If a connectivity check fails, a saved key remains in place and Actor-si continues in simulator mode; a later restart can use the real provider once connectivity is available.

### When you hit 429 (quota exceeded)

Preview models can return `429 RESOURCE_EXHAUSTED / Quota exceeded`. Actor-si can rotate through configured keys and, if all keys are limited for the requested model, retry with configured fallback models.

1. **Rotate keys.** A key that receives 429, 401, or 403 is cooled down for `ACTOR_KEY_COOLDOWN` seconds (default: 60; a longer server retry delay takes precedence). Actor-si reports when every key is limited instead of inventing a response. Use `actor key stats` to inspect counts; raw keys are not written to logs or state files.
2. **Use a fallback model.** The quota is scoped by project and model. `ACTOR_MODEL_FALLBACKS` defaults to `gemini-3.5-flash,gemini-2.5-flash`. Actor-si announces a switch; use `--no-model-fallback` to disable it for one run.
3. **Review your provider and quota.** `actor key sources` lists official access options. Creating another key in the same project does not increase that project's quota. Do not use multiple accounts to evade provider limits, and do not send sensitive data to a free-tier service unless its data terms are acceptable.

For Vertex AI Express Mode, set `ACTOR_BASE_URL` to `https://aiplatform.googleapis.com`; Actor-si selects the corresponding API version and model path. See [API key management](docs/keys.md) for configuration details.

| Task | Command |
| --- | --- |
| Start an interactive CLI session | `python3 -m actor` |
| Start the web UI and HTTP API | `python3 -m actor serve --port 8080` |
| Run one request | `python3 -m actor "Review this diff"` |
| Return JSON | `python3 -m actor --json "Summarize this meeting"` |
| List skills | `python3 -m actor skills list` |
| List tools | `python3 -m actor tools` |
| Attach a file | `python3 -m actor --attach ./diagram.png "Explain this diagram"` |
| Check configuration | `python3 -m actor doctor --check-api` |

Run `pip install -e .` if you want the `actor` console command.

## Always-on bot

An always-on bot is a full Actor-si instance with its own local skills, model settings, and session history. A room is the shared log where people and bots meet.

```bash
# Terminal 1: start the room server
python3 -m actor serve --port 8080

# Terminal 2: start a bot with a persona skill
python3 -m actor bot --room team --skills mika --name Mika \
  --skills-dir skills --skills-dir examples/mika --from-now

# Terminal 3: post a message
curl -s -X POST localhost:8080/api/rooms/team/messages \
  -H 'Content-Type: application/json' \
  -d '{"text":"Please review the release plan","author":"team-member"}'

# Inspect the conversation
python3 -m actor rooms show team
```

Edit a skill while the bot is running; it reloads `SKILL.md` on the next turn. Use `--mention` to route messages among multiple bots in one room. The room is append-only, and bots resume from a saved cursor. See the [bot operations guide](docs/bot.md) for `systemd`, `cron`, and Windows Task Scheduler examples.

For a one-off server-side reply without a resident bot, send `{"reply":true}` to `POST /api/rooms/<name>/messages`.

## Tools (function calling)

The model can choose from the enabled tools, which are recorded in the conversation. Read-only tools are enabled by default; writing files and network access require explicit opt-in.

| Tool | Purpose | Default |
| --- | --- | --- |
| `now` | Current date, time, and timezone | Enabled |
| `read_file` / `list_files` | Read and list files under `ACTOR_TOOLS_ROOT` | Enabled |
| `list_skills` / `read_skill` | Discover and read loaded skills | Enabled |
| `write_file` | Write a file (`ACTOR_TOOLS_ALLOW_WRITE=true`) | Disabled |
| `http_get` | Make an external GET request (`ACTOR_TOOLS_ALLOW_NETWORK=true`) | Disabled |

Tool paths cannot escape `ACTOR_TOOLS_ROOT`; traversal and absolute paths are rejected. A skill's `allowed-tools` can narrow the available set while that skill is active. The number of tool rounds is bounded by `ACTOR_MAX_TOOL_ROUNDS` (default: 4).

## Ask with images and PDFs

```bash
python3 -m actor --attach ./design.png "List three usability issues"
python3 -m actor --attach ./spec.pdf --attach ./notes.md "Find gaps in this specification"
python3 -m actor rooms say "Review this diagram" --attach ./diagram.png --reply
```

The web UI accepts attachments through its paperclip button. The HTTP API accepts `attachments: [{name, mime_type, data}]`, where `data` is base64, on `/api/chat` and `/api/rooms/<name>/messages`. Defaults allow up to six attachments of 10 MB each (`ACTOR_MAX_ATTACHMENTS`, `ACTOR_MAX_ATTACHMENT_MB`). Attachments are sent to the provider as inline data; set `ACTOR_STORE_ATTACHMENTS=false` to keep them only for the current turn. Room-list endpoints do not return base64; request `?attachments=1` when the contents are needed.

## Portable Agent Skills / Agensi

For the Agensi-specific export profile, lint limitations, and portable `SKILL.md` compatibility details, see the [Agensi workflow](docs/agensi.md) and [compatibility notes](docs/skills-compat.md). Lint is a conservative approximation, not a guarantee of acceptance.

## Protect rooms with tokens and webhooks

Set `ACTOR_ROOM_TOKEN` to require authentication on every `/api/*` endpoint. It is mandatory when binding to a non-loopback interface. Store the token in a local secret manager or environment; do not put a literal token in source, logs, fixtures, or documentation. If you use a webhook, keep its signing secret separate.

```bash
export ACTOR_ROOM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export ACTOR_WEBHOOK_URL="https://example.com/actor-hook"
export ACTOR_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m actor serve --port 8080
```

```bash
curl -s -X POST localhost:8080/api/rooms/team/messages \
  -H "X-Actor-Token: ${ACTOR_ROOM_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello"}'
```

Invalid or missing tokens receive **403**. The web UI prompts for a token if the server requires one. API clients can use `X-Actor-Token` or `Authorization: Bearer`. Tokens in URL query strings are not accepted. Webhook messages are sent asynchronously as JSON; failures do not interrupt the conversation, and transient server errors are retried up to three times. The signature is `HMAC-SHA256(secret, body)` in the `sha256=<hex>` format. Inspect `/api/health` for `webhook_stats`.

## CLI

```text
$ python3 -m actor
Actor-si v0.2.1 — SKILL.md-driven Gemini client
Use /help for commands and /exit to quit.
Running in simulator mode; configure an API key locally to use Gemini.
5 skills / data directory: /path/to/Actor-si/.actor

you [-,auto|think] › Write release notes from these commits
▶ Selected skill: release-notes
  system_instruction: 4,102 characters
Streaming response...
tokens: in 1,240 / out 812  model: gemini-3-flash
```

### Invoke skills

| Method | Example | Behavior |
| --- | --- | --- |
| Call with `/` | `/minutes-writer Turn these notes into minutes` | Applies for this turn only |
| Pin a stack | `/use code-review brand-voice` | Applies on later turns until cleared |
| Automatic selection | Enter a request without a skill command | Selects skills whose score meets the threshold |
| Clear pinned skills | `/use none` | Removes pinned skills |

REPL commands include `/help`, `/skills`, `/use`, `/auto`, `/think`, `/stream`, `/model`, `/system`, `/show`, `/new`, `/sessions`, `/load`, `/title`, `/create`, `/export`, and `/exit`.

Other useful features include `/create` to draft a skill from a conversation, `/export` to create a ZIP, `--json` for scripts and CI, and piping input with `cat meeting.txt | python3 -m actor "Create minutes"`.

## Web UI

Run `python3 -m actor serve` and open `http://localhost:8080`.

- **Left:** skill list (click to pin a stack, `ⓘ` for details) and conversation history.
- **Center:** chat; type `/` to browse skills and use the arrow keys plus Enter to select one.
- **Right:** inspector showing applied skills, auto-selection scores, the composed `system_instruction`, and token counts.
- **In responses:** thinking log and chips identifying the skills used.
- **Create a skill from chat:** describe the task, review the draft, and edit it before saving.

The UI is plain HTML, CSS, and JavaScript in `actor/web/`; it needs no build step, CDN, or external fonts.

## HTTP API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/health` | Health, provider, skill count, and key count |
| GET | `/api/config` | Settings (never returns raw keys) |
| GET/POST/DELETE | `/api/key` | Inspect, save, or clear local key settings |
| GET | `/api/skills?q=minutes` | List skills and optional selection scores |
| GET | `/api/skills/<name>?full=1` | Skill details and composed instruction |
| POST | `/api/skills` | Create a skill: `{name, description, instructions}` |
| PUT / DELETE | `/api/skills/<name>` | Update or delete a skill |
| POST | `/api/skills/draft` | Draft a `SKILL.md` from `{request}` |
| GET | `/api/skills/export?names=a,b` | Export selected skills as a ZIP |
| POST | `/api/select` | Preview selection for `{message, skills}` without calling a model |
| GET / POST | `/api/sessions` | List or create conversations |
| GET / DELETE | `/api/sessions/<id>` | Read or delete a conversation |
| GET / POST | `/api/rooms` | List or create rooms |
| GET | `/api/rooms/<name>?after=<seq>` | Read room updates; add `attachments=1` for attachment data |
| POST | `/api/rooms/<name>/messages` | Append a message; `reply:true` generates a server-side reply |
| DELETE | `/api/rooms/<name>` | Delete a room |
| POST | `/api/chat` | Run one JSON chat turn |
| POST | `/api/chat/stream` | Run one chat turn and stream tokens as SSE |

```bash
# Preview automatic selection without calling a model
curl -s localhost:8080/api/select -H 'Content-Type: application/json' \
  -d '{"message":"Write release notes from these commits"}' | jq

# Run one turn
curl -s localhost:8080/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"Create meeting minutes","skills":["minutes-writer"]}' | jq -r .text

# Stream a turn using server-sent events
curl -N localhost:8080/api/chat/stream -H 'Content-Type: application/json' \
  -d '{"message":"Review this diff"}'
```

Stream events are emitted in this order as applicable: `open`, `selection`, `prompt`, `thought`, `tool_call`, `tool_result`, `text`, and `done` (usage, tool calls, and session ID).

## Writing `SKILL.md`

```text
skills/
  minutes-writer/
    SKILL.md              # Required at the skill folder root
    references/
      format.md           # Optional text reference, included in system instructions
      style.pdf           # Optional binary reference, sent as inline data
  code-review.md          # A standalone Markdown file is also supported
```

A skill uses YAML frontmatter and a Markdown body. Required output headings are a contract; write the body of the response in the user's language.

````markdown
---
name: minutes-writer
description: Creates structured meeting minutes. Use when the user provides meeting notes or a transcript.
license: MIT
metadata:
  version: "1.0.0"
  tags: [meeting, minutes]
temperature: 0.4
---

# Purpose
State what the skill achieves.

# Input
- List required materials.

# Procedure
1. Start each step with a concrete action.
2. Use enough detail for another person to produce the same result.

# Output format
```markdown
## Summary
```

# Checklist
- Do not invent or exaggerate facts.
````

Use lowercase kebab-case for `name`; keep the folder name identical. Actor-si supports per-skill generation settings such as `temperature`, `top_p`, `max_output_tokens`, `thinking_budget`, and `model`. A text reference is included in the `system_instruction` up to `ACTOR_INLINE_REFERENCE_LIMIT` (default: 24,000 characters); images and PDFs are sent as inline data. References are size-limited, and nested code fences are wrapped safely when composed into instructions.

## Automatic skill selection

Actor-si deterministically scores the request against skill names, descriptions, and tags using English words and character bigrams.

| Match | Score |
| --- | ---: |
| Explicit `/name` or `@name` | +100 |
| Skill name appears in the request | +5 |
| Exact or partial name match | +5 / +3 |
| Description word or bigram match | +1 to +3 |
| Tag match | +1.5 |

Skills scoring at least the default threshold (`2.0`) are selected, up to three. Configure `ACTOR_AUTO_SELECT_THRESHOLD` and related settings; inspect scores in the web UI.

## Configuration

Settings can come from `.env`, environment variables, and CLI options. Precedence is **CLI > environment > `.env`**.

### Provider choices

| `--provider` | Implementation | Extra dependency | Use |
| --- | --- | --- | --- |
| `auto` (default) | `gemini` when a key is present, otherwise `mock` | None | Recommended for most use |
| `gemini` | REST via `urllib` | None | Keep runtime dependencies minimal |
| `genai` | Official `google-genai` SDK | `pip install google-genai` | Use the official SDK backend |
| `mock` | Built-in simulator | None | Offline checks and UI development |

Installing `google-genai` enables the SDK backend automatically unless `ACTOR_GEMINI_IMPL` specifies otherwise. `actor doctor` reports where the key was loaded from.

| Variable | Default | Description |
| --- | --- | --- |
| `GEMINI_API_KEY` / `GEMINI_API_KEYS` | — | One or more provider keys |
| `ACTOR_API_KEY_FILE` | `./.actor/api_key` | Key file used by the UI and `actor key set` |
| `ACTOR_KEYS_DIR` | `./.actor/keys` | Per-key files used by `actor key add` |
| `ACTOR_KEY_COOLDOWN` / `ACTOR_KEY_ROTATE` | `60` / `true` | Cooldown and rotation behavior after rate limits or authorization errors |
| `ACTOR_PROVIDER` / `ACTOR_GEMINI_IMPL` | `auto` / `gemini` | Provider selection and implementation |
| `ACTOR_MODEL` | `gemini-3-flash` | Model name |
| `ACTOR_MODEL_FALLBACKS` | `gemini-3.5-flash,gemini-2.5-flash` | Models to try after the primary model is rate-limited; empty disables fallback |
| `ACTOR_BASE_URL` | `https://generativelanguage.googleapis.com` | Compatible endpoint; Vertex uses `https://aiplatform.googleapis.com` |
| `ACTOR_API_VERSION` / `ACTOR_MODEL_PATH` | `v1beta` / `models/{model}` | API version and model path; Vertex Express Mode is detected automatically |
| `ACTOR_TEMPERATURE` / `ACTOR_TOP_P` / `ACTOR_MAX_OUTPUT_TOKENS` | `0.7` / `0.95` / `4096` | Generation settings |
| `ACTOR_THINKING_BUDGET` | `0` | Enable thinking configuration when greater than zero |
| `ACTOR_SKILLS_DIR` | `./skills` | Skill directories; multiple paths are supported |
| `ACTOR_AUTO_SELECT` / `ACTOR_MAX_AUTO_SKILLS` / `ACTOR_AUTO_SELECT_THRESHOLD` | `true` / `3` / `2.0` | Automatic-selection controls |
| `ACTOR_INLINE_REFERENCE_LIMIT` | `24000` | Maximum reference text included in instructions |
| `ACTOR_DATA_DIR` / `ACTOR_HOST` / `ACTOR_PORT` | `./.actor` / `127.0.0.1` / `8080` | Data directory and server address |
| `ACTOR_TOOLS` / `ACTOR_TOOLS_ROOT` | Read-only defaults / `.` | Enabled tools and filesystem sandbox root |
| `ACTOR_TOOLS_ALLOW_WRITE` / `ACTOR_TOOLS_ALLOW_NETWORK` | `false` | Explicitly enable file writes or network requests |
| `ACTOR_TOOLS_TIMEOUT` / `ACTOR_MAX_TOOL_ROUNDS` | `15` / `4` | Tool timeout and bounded tool rounds |
| `ACTOR_MAX_ATTACHMENT_MB` / `ACTOR_MAX_ATTACHMENTS` | `10` / `6` | Attachment limits |
| `ACTOR_STORE_ATTACHMENTS` | `true` | Store attachment data in local session and room files |
| `ACTOR_ROOM_TOKEN` / `ACTOR_ROOM_PROTECT_READS` | — / `false` | Protect API access; the read-only legacy option remains for compatibility |
| `ACTOR_CORS_ORIGINS` / `ACTOR_ALLOWED_HOSTS` | Empty | Explicit browser origins and hostnames allowed by the server |
| `ACTOR_WEBHOOK_URL` / `ACTOR_WEBHOOK_SECRET` | — | Asynchronous, HMAC-signed room notifications |
| `ACTOR_WEBHOOK_EVENTS` / `ACTOR_WEBHOOK_TIMEOUT` | `message` / `10` | Webhook events and timeout |

Sessions and room data are stored as local JSON under `.actor/`, which is ignored by Git. Retries use exponential backoff for server errors; rate limits rotate keys and may fall back to another model.

## Project structure

```text
actor/                 CLI, HTTP server, chat, skill loading, providers, and tools
actor/web/             Plain HTML, CSS, and JavaScript UI
actor/resources/       Shared portable-skill resources
skills/                Five English sample skills (default)
i18n/ja/skills/        Japanese skills; opt in with --skills-dir
examples/mika/         Example persona for a resident bot
README.ja.md            Japanese README
HANDOFF.md              Current project handoff
LISTING.md              Agensi listing draft
docs/                   English guides and reference documentation
docs/ja/                Japanese documentation
tests/                  Standard-library tests and optional UI smoke test
```

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m actor skills lint --target agensi
python3 -m actor skills test
python3 -m actor skills --skills-dir i18n/ja/skills lint --target agensi
python3 -m actor skills --skills-dir i18n/ja/skills test
python3 -m actor skills export minutes-writer --target agensi -o /tmp/minutes-writer.zip
```

The `skills test` commands run format-only checks; they do not call a model. `--live` uses the configured model and may incur charges. To verify the optional token-protected UI smoke test, install Node and `jsdom`, generate a temporary token locally, start the server with that token, and run `node tests/ui_smoke.mjs` with the same environment variable. CI performs this smoke test with an ephemeral token.

## Security and cautions

- Skills are local Markdown instructions. Actor-si does not execute scripts embedded in skills; it sends skill text to the provider, which may process it as instructions.
- Do not place confidential material in a skill: the body and text references are sent to the provider.
- Review generated minutes and other outputs before sharing them externally.
- The server binds to `127.0.0.1` by default. Set `ACTOR_ROOM_TOKEN` before binding to a non-loopback interface; startup is refused without it.
- When configured, the token protects every `/api/*` endpoint. Use `X-Actor-Token` or `Authorization: Bearer`; query-string tokens are not accepted.
- CORS is disabled by default. Set `ACTOR_CORS_ORIGINS` only for browser access from an explicitly trusted origin. Host checks allow loopback, the configured bind host, and `ACTOR_ALLOWED_HOSTS`.
- Tools are read-only by default. `write_file` and `http_get` require explicit opt-in, and file access is confined to `ACTOR_TOOLS_ROOT`.
- Image and PDF attachments are sent to the configured provider; review them for sensitive content before uploading.

### Secret management

Keep API keys, room tokens, and webhook secrets out of version control and logs. If one is exposed, revoke or rotate it immediately. For non-local deployments, use a trusted TLS-terminating reverse proxy and avoid exposing unnecessary ports. See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## References

- [Agensi-compatible skills](docs/agensi.md)
- [SKILL.md compatibility](docs/skills-compat.md)
- [API key management and quota rotation](docs/keys.md)
- [Always-on bot operations](docs/bot.md)
- [Funding policy](FUNDING.md)
- [Japanese edition](README.ja.md)
- [Japanese documentation](docs/ja/)
- [Project handoff](HANDOFF.md)
- [Changelog](CHANGELOG.md)

## Mapping to the reference SDK

```python
from google import genai
from google.genai import types

client = genai.Client()
chat = client.chats.create(
    model="gemini-2.5-flash",
    config=types.GenerateContentConfig(
        system_instruction=load_skill_prompt("./skills/mika/SKILL.md"),
        temperature=0.85,
    ),
)
response = chat.send_message("Hello.")
```

| Reference SDK | Actor-si |
| --- | --- |
| `load_skill_prompt("./skills/mika/SKILL.md")` | `SkillLibrary(...).get("mika")`; multiple skills are composed into one instruction |
| `temperature=0.85` | Set `temperature: 0.85` in the skill frontmatter |
| `client.chats.create(...)` | `ChatEngine` plus local `.actor/sessions/*.json` |
| `chat.send_message(...)` | `actor "Hello"`, `POST /api/chat`, or a room message |
| `genai.Client()` | `--provider genai` (SDK) or `--provider gemini` (standard-library REST) |
| Function calling tools | Skill `allowed-tools` and `ACTOR_TOOLS`, executed by `ChatEngine.turn()` |
| Image or PDF parts | `--attach`, the UI attachment button, or the API `attachments` field |

The optional SDK path is covered by regression tests when `google-genai` is installed; the core package has no mandatory dependencies.

## License

MIT. See [LICENSE](LICENSE).
