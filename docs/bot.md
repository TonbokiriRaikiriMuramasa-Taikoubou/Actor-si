# Always-on bot operations guide

Actor can run as an interactive client or as an always-on bot. This guide covers the bot process.

```text
┌──────────────┐  poll for room updates  ┌────────────────────────────┐
│  actor bot   │ ─────────────────────▶ │  actor serve               │
│  local host  │ ◀───────────────────── │  /api/rooms/<name>         │
│  SKILL.md    │   post a reply          │  (shared room log)          │
│  + Gemini    │                         └────────────────────────────┘
└──────────────┘                                      ▲
                           people and apps post messages via HTTP
```

The machine running the bot needs provider credentials. Store them locally in `.env`, through `actor key set`, or in your service manager; never paste a key value into a command or document. Use `actor key show` or `actor doctor` to inspect the active source. Resident bots may reach provider rate limits: configure multiple keys or fallback models and inspect state with `actor key list` / `actor key check`.

A bot is a full Actor instance with local skills, sessions, and history. A room is only the shared meeting point, so bots with different skills or models can join the same room.

## 1. Minimal setup (three steps)

```bash
# 1) Start the server
python3 -m actor serve --port 8080

# 2) Start a resident bot with a persona skill
python3 -m actor bot --room actor-x --skills mika --name Mika \
  --skills-dir skills --skills-dir examples/mika

# 3) Post a message
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'Content-Type: application/json' \
  -d '{"text":"Please summarize today’s tasks","author":"team-member"}'

# Inspect the conversation
python3 -m actor rooms show actor-x
```

A one-off server-side response does not need a resident bot; add `reply: true`:

```bash
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'Content-Type: application/json' \
  -d '{"text":"Organize today’s tasks","reply":true}' | jq -r '.reply.text'
```

| | Bot (`actor bot`) | Server-side (`reply: true`) |
| --- | --- | --- |
| Skills | Bot's local skills | Server's skills |
| Model / temperature | Bot settings plus skill overrides | Same |
| Best for | Persistent work, multiple rooms, and tuning skills | One-off checks and calls from other apps |

Bots can also use function-calling tools. For example, a bot may call `now` or `read_file`; the call is recorded in the log. Inspect enabled tools with `actor tools`.

```bash
# Restrict tools and their filesystem scope (read-only by default)
ACTOR_TOOLS=now,read_file,list_files python3 -m actor bot --room actor-x --skills mika
ACTOR_TOOLS_ROOT=~/workspace
ACTOR_TOOLS_ALLOW_WRITE=false
ACTOR_TOOLS_ALLOW_NETWORK=false
```

Images and PDFs can be sent through the same room API:

```bash
python3 -m actor rooms say "Review this diagram" --attach ./diagram.png --reply
```

## 2. Bot options

```bash
python3 -m actor bot \
  --room actor-x \                    # room to monitor
  --api http://127.0.0.1:8080 \       # server URL
  --name Mika \                       # display name in the room
  --skills mika --skills brand-voice \ # pin one or more skills
  --interval 1.5 \                    # polling interval in seconds
  --mention @actor \                  # respond only when this text is present
  --from-now \                        # ignore messages posted before startup
  --stream \                          # also stream generated text to the terminal
  --model gemini-3-pro \              # choose a model
  --thinking-budget 2048               # enable a thinking budget
```

- `--no-auto` disables automatic skill selection and uses only pinned skills.
- `--once` handles one update and exits; use it with cron or Task Scheduler.
- The read cursor is saved to `.actor/bot-<room>.json` and resumes after restart.

### Conversation continuity after restart

A bot's replies are associated with a session for its room under `.actor/sessions/*.json`. Restarting the process preserves context for the same room.

## 3. Edit `SKILL.md` while the bot is running

The bot reloads its skill files on every turn. Edit the skill while the process is running:

```bash
$EDITOR examples/mika/SKILL.md
```

Changes apply to the next reply. This is the fastest way to tune a persona or procedure. Change pinned skills by restarting with different `--skills`, or add another named bot to the same room.

## 4. Ways to run the bot

### Keep it running in a terminal (simplest)

```bash
python3 -m actor bot --room actor-x --skills mika
# Press Ctrl-C to stop
```

### Use `nohup` or the background

```bash
nohup python3 -m actor bot --room actor-x --skills mika \
  > .actor/bot.log 2>&1 &
tail -f .actor/bot.log
```

### Use systemd (Linux)

```ini
# ~/.config/systemd/user/actor-bot.service
[Unit]
Description=Actor bot (mika)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/Actor
EnvironmentFile=%h/Actor/.env
ExecStart=/usr/bin/python3 -m actor bot --room actor-x --skills mika --from-now
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now actor-bot
journalctl --user -u actor-bot -f
```

### Use cron with `--once`

```cron
*/5 * * * * cd "$HOME/Actor" && /usr/bin/python3 -m actor bot --room actor-x --skills mika --once >> .actor/cron.log 2>&1
```

### Use Windows Task Scheduler

```powershell
schtasks /create /tn "ActorBot" /sc minute /mo 5 /tr ^
 "cmd /c cd %USERPROFILE%\Actor && python -m actor bot --room actor-x --skills mika --once"
```

## 5. Multiple bots and rooms

```bash
# Two personas in one room; use mentions to route requests
python3 -m actor bot --room team --name Mika --skills mika --mention @mika
python3 -m actor bot --room team --name Reviewer --skills code-review --mention @review

# Separate rooms for separate tasks
python3 -m actor bot --room work --skills minutes-writer --api http://127.0.0.1:8080
python3 -m actor bot --room devlog --skills release-notes --interval 5
```

When multiple bots share a room, use `--mention` to route a message; without it, every bot may respond to the same message.

## 6. API reference

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/rooms` | List rooms |
| POST | `/api/rooms` | Create a room with `{name, skills}`; returns an existing room if present |
| GET | `/api/rooms/<name>?after=<seq>&limit=<n>` | Read updates; `cursor` reports the latest sequence; `attachments=1` includes attachment data |
| POST | `/api/rooms/<name>/messages` | Append `{text, author, role, skills, reply, attachments, tools}` |
| DELETE | `/api/rooms/<name>` | Delete a room |

When `ACTOR_ROOM_TOKEN` is set, **every `/api/*` endpoint** requires `X-Actor-Token` or `Authorization: Bearer`. The bot can read the token from its environment; avoid placing a token literal in arguments, logs, or documentation. External binding requires a token; the default bind address is `127.0.0.1`.

With `reply: true`, the server calls its configured model and includes `reply` in the response. The response's `missing_skills` field lists skills that the server could not find, which helps detect a missing `--skills-dir`.

## 6.5 Token protection and webhooks

Generate tokens locally and store them in an environment or secret manager. Keep the room token and webhook signing secret separate.

```bash
# Terminal 1: protect the server and enable webhook delivery
export ACTOR_ROOM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export ACTOR_WEBHOOK_URL="https://example.com/actor-hook"
export ACTOR_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m actor serve --port 8080

# Terminal 2: load the same value from your local secret store into ACTOR_ROOM_TOKEN,
# then run the bot (it reads the token from its environment).
python3 -m actor bot --room actor-x --skills mika --api http://127.0.0.1:8080
```

Webhook payloads use `Content-Type: application/json`:

```json
{"event":"message","ts":"2026-10-13T09:00:00+0900","room":"actor-x",
 "message":{"seq":3,"role":"user","text":"Please summarize this","author":"team-member"},
 "reply":null,"count":3}
```

Verify the signature on the receiving side (Node.js example):

```js
const crypto = require('crypto');
const expected = 'sha256=' + crypto.createHmac('sha256', process.env.ACTOR_WEBHOOK_SECRET)
  .update(rawBody).digest('hex');
if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(req.headers['x-actor-signature']))) return res.status(401).end();
```

- Delivery is asynchronous. A delivery failure does not stop the conversation; 5xx and 429 responses are retried up to three times.
- Model fallback works for bots as well: when rate limits persist, Actor tries models in `ACTOR_MODEL_FALLBACKS` order. Use `--no-model-fallback` to disable it.
- Inspect `queued`, `sent`, `failed`, and `skipped` counters in `GET /api/health` under `webhook_stats`.
- To relay messages to another service, forward `message.text` only after applying the receiving service's own validation and safety rules.

## 7. Common issues

| Symptom | Cause and resolution |
| --- | --- |
| Bot does not respond | Check that the server is running (`curl localhost:8080/api/health`). For a remote server, set `--api`. |
| Persona has no effect | The skill may not be loaded. Check startup warnings and `missing_skills`; add the required `--skills-dir`. |
| Bot replies to old messages | Add `--from-now` or adjust the cursor in `.actor/bot-<room>.json`. |
| Cannot connect to server | Check the `serve` host/port and the bot's `--api` URL. External binding requires a token. |
| Duplicate replies | Make sure the same bot is not running twice for one room; processes share `.actor/bot-<room>.json`. |
| Slow response | Use `--stream` to see progress, choose a Flash model, or reduce `--interval`. |

## 8. Security

- The default bind is `127.0.0.1`. Binding to a non-loopback address requires `ACTOR_ROOM_TOKEN`, which protects every API endpoint.
- CORS is disabled by default. Set `ACTOR_CORS_ORIGINS` only when a trusted cross-origin browser client is needed; add LAN hostnames or IPs to `ACTOR_ALLOWED_HOSTS`.
- If the service is exposed externally, use a trusted TLS-terminating reverse proxy where appropriate.
- Tools are read-only by default. `write_file` and `http_get` require explicit `ACTOR_TOOLS_ALLOW_*` settings; filesystem access is confined to `ACTOR_TOOLS_ROOT`.
- Room logs and sessions are stored as plaintext JSON under `.actor/` (ignored by Git).
- Skill bodies and references are sent to the model API on each applicable turn; do not put confidential material in them.
