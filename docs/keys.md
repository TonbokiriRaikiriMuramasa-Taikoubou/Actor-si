# Managing API keys and automatic 429 rotation

Preview models can exceed free-tier quotas and return `429 RESOURCE_EXHAUSTED / Quota exceeded`. Actor can keep several keys and retry a request with the next available one.

- A single key (`GEMINI_API_KEY`) remains supported.
- A key that receives 429 is cooled down for `ACTOR_KEY_COOLDOWN` seconds (default: 60).
- If all keys are limited, Actor reports when a key is expected to recover instead of returning fabricated output.
- Raw key values are not written to logs, errors, or the state file; those surfaces use masked values or fingerprints.

## 1. Key sources and precedence

Actor checks the following sources in order. Duplicate values are consolidated, keeping the first occurrence.

| Priority | Source | Setting / path | Notes |
| ---: | --- | --- | --- |
| 1 | CLI option | `--api-key` | One-run override; avoid putting secrets in shell history. |
| 2 | Environment, multiple | `GEMINI_API_KEYS` | Comma, whitespace, or semicolon separated. |
| 3 | Environment, single | `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or `GOOGLE_GENAI_API_KEY` | Useful for CI and service managers. |
| 4 | `.env` | `GEMINI_API_KEYS` or `GEMINI_API_KEY` | Repository-root file; ignored by Git. |
| 5 | Saved key file | `.actor/api_key` | Written through the UI or `actor key set`; one key per line. |
| 6 | Key directory | `.actor/keys/key-*.key` | Written by `actor key add`; change the location with `ACTOR_KEYS_DIR`. |

`.actor/` and `*.key` are ignored by Git. Still keep secrets out of source files and logs.

## 2. Common commands

```console
$ python3 -m actor key list
API keys: 3 (2 available)
  [0] [masked] active
  [1] [masked] ready
  [2] [masked] cooling down

$ python3 -m actor key add --check
$ python3 -m actor key add --env
$ python3 -m actor key check --all
$ python3 -m actor key stats
$ python3 -m actor key rm 2
$ python3 -m actor key clear
```

`key set` saves one key to `.actor/api_key`. For multiple keys, use `key add` (one file per key), `key add --env` (the `GEMINI_API_KEYS` entry in `.env`), or the `GEMINI_API_KEYS` environment variable. These sources can be combined; duplicates are removed.

### Append to `.env` with `key add --env`

```console
$ python3 -m actor key add --env
$ cat .env
# Existing comments and ordering are preserved.
GEMINI_API_KEYS=<local values are not shown>
ACTOR_MODEL=gemini-3-flash
```

- Re-adding the same key does not create duplicates.
- Existing `export` syntax and either the singular or plural variable are handled. `key add --env` uses the plural variable by default.
- If `.env` does not exist, Actor creates it with restrictive permissions where supported.
- `key rm <index>` can remove a value from `.env`; the variable itself may remain with no values. An environment variable set in a parent shell cannot be edited by Actor.

## 2.5 Which keys hit limits most often? (`actor key stats`)

Actor stores `uses`, `errors`, `limits` (429 count), and aggregate counters so you can inspect repeated rate limits later.

```console
$ python3 -m actor key stats
Key usage statistics (.actor/key_state.json)
  Totals: 120 requests / 14 rate limits / 14 rotations
  By model
    gemini-3.8-flash     12 limits / 40 requests / 1 of 2 keys ready
    gemini-3.5-flash      2 limits / 80 requests / 2 of 2 keys ready
  Most limited keys
  [0] [masked] 12 limits / 40 attempts (30%) active [gemini-3.8-flash]
  [1] [masked]  2 limits / 80 attempts (3%) ready

$ python3 -m actor key stats --json
$ python3 -m actor key stats --reset
```

| Pattern | Meaning | Action |
| --- | --- | --- |
| One key gets most 429s | It may be shared with another app or may be disabled | Add a dedicated key and verify its status |
| All keys get similar 429s | The project's total quota may be insufficient | Review quota or configure model fallback |
| One model gets most 429s | That model has a smaller quota | Adjust `ACTOR_MODEL_FALLBACKS` |
| Many 401 / 403 errors | A key may be invalid or revoked | Remove it with `key rm` |
| Very few attempts | The key may have been added recently | Gather more data before drawing conclusions |

`actor doctor` and `actor key list` also summarize rate limits. JSON output includes only masked values and state metadata, not raw keys.

## 3. What happens after a 429

```text
generateContent / streamGenerateContent
        │
        ├─ 200             → mark the key healthy and increment usage
        ├─ 5xx / timeout   → exponential backoff and retry the same key (up to 3 times)
        ├─ 429 / 401 / 403 → cool down that key and try the next available key
        └─ other errors    → return the error without cycling through keys
```

- Actor selects an available, non-limited key and stops after one full rotation.
- If an SSE response has already started, Actor does not switch keys mid-response; that turn fails rather than mixing output. A later turn can select a different key.
- Set `ACTOR_KEY_ROTATE=false` to disable rotation. Set `ACTOR_KEY_COOLDOWN=0` to avoid remembering cooldowns.

When every key is limited, CLI, UI, and JSON responses include the estimated recovery times and the relevant status. Use `actor key list` to check state or `actor key add` to add a key.

## 3.5 Model fallback (`ACTOR_MODEL_FALLBACKS`)

Gemini quotas are scoped by project and model. If every key is limited for the primary model, Actor can try fallback models with the same configured credentials.

| Setting | Default | Description |
| --- | --- | --- |
| `ACTOR_MODEL_FALLBACKS` | `gemini-3.5-flash,gemini-2.5-flash` | Ordered fallback models; empty disables fallback |
| `--fallback-model <name>` | — | Add a fallback for this run; can be repeated |
| `--no-model-fallback` | — | Disable fallback for this run |

Behavior:

- The requested primary model is tried first while a key is available; fallback is for rate limits.
- If the primary model is already known to be limited for every key, Actor starts with a fallback instead of sending a request that is expected to fail.
- Cooldowns are tracked per key and model in `key_state.json`.
- Actor announces the selected fallback in CLI output, JSON notices, or the web UI; it never switches silently.
- An error is returned only when all configured models are unavailable.
- A skill or `--model` override is tried first, then the fallback list.

```text
Primary model → try available keys
  └ all limited → fallback 1 → try available keys
       └ all limited → fallback 2 → …
            └ all unavailable → report the models and recovery times
```

Fallback models can have different cost, availability, and quality. Choose them to fit your workload.

## 3.6 Official ways to obtain provider credentials (`actor key sources`)

`actor key sources` lists official access options. Creating another key within the same cloud project does not increase that project's quota.

| Provider option | Access | Notes |
| --- | --- | --- |
| **Google AI Studio** | Free-tier access where available | Quotas are per project and model; see current provider terms. |
| **Vertex AI Express Mode** | Eligible new cloud users may receive a limited free period | Supports API-key authentication and a separate endpoint. |
| **Google Cloud free trial** | Promotional credits may be available | Billing setup and eligibility conditions apply; use Vertex AI. |
| **Organization or Workspace administrator** | A project key may be issued by an administrator | Follow your organization's policy. |
| **Startup, research, or student programs** | Credits may be available | Eligibility and review requirements vary. |

Do not evade provider limits by creating multiple accounts or buying/shared credentials. Review provider data-use terms before sending sensitive content; free-tier requests may be used to improve products.

### Use Vertex AI Express Mode

Express Mode uses the `aiplatform.googleapis.com` endpoint and `publishers/google/models/...` paths. When `ACTOR_BASE_URL` contains that host, Actor selects the corresponding API version and model path automatically.

```bash
# Set GEMINI_API_KEY from your local secret store before running this command.
export ACTOR_BASE_URL="https://aiplatform.googleapis.com"
python3 -m actor key check --all
python3 -m actor --model gemini-2.5-flash "Hello"
```

The optional `--provider genai` SDK path may assume v1beta in some flows. For Express Mode, use the standard-library REST provider unless you have verified the SDK configuration for your endpoint.

## 4. State file (`.actor/key_state.json`)

To remember cooldowns across processes, Actor stores a SHA-256 fingerprint (the first 12 characters) of each key, not the raw key.

```json
{
  "version": 2,
  "totals": {"requests": 120, "limits": 14, "rotations": 14},
  "by_model": {
    "gemini-3.8-flash": {"requests": 40, "limits": 12},
    "gemini-3.5-flash": {"requests": 80, "limits": 2}
  },
  "keys": {
    "<sha256-fingerprint>": {
      "models": {
        "gemini-3.8-flash": {"limited_until": 0, "status": 429, "reason": "Quota exceeded", "uses": 12},
        "gemini-3.5-flash": {"limited_until": 0, "status": null, "reason": "", "uses": 8}
      }
    }
  }
}
```

- Model-specific state is stored beneath `models`; a legacy version 1 file is migrated when read.
- `by_model` aggregates limits and requests for `actor key stats`.
- Deleting the file is safe; Actor recreates it. Expired cooldowns become available automatically.
- `actor key clear` does not delete statistics. Use `actor key stats --reset` to reset them.
- Removed keys leave fingerprints only; stats reports when such records remain.

## 5. Masking and redaction

| Surface | Information exposed |
| --- | --- |
| CLI (`key list`, `show`, `check`, `doctor`) | Masked key only |
| Error messages | Provider text is redacted before display |
| `/api/config` / `/api/health` | Presence and counts only |
| `/api/key` | Masked value, source, and status/statistics |
| `.actor/key_state.json` | Fingerprint and non-secret usage state |
| `Settings` representation | Key fields are excluded from `repr` |

If a credential may have been exposed, revoke it with the provider, remove its local copy, and rotate any related room token.

## 6. Tests

```bash
python3 -m unittest discover -s tests -v
```

The unit suite covers key collection and precedence, de-duplication, cooldowns, masking and state files, concurrent access, safe `.env` updates, model fallback, Vertex URL selection, CLI key management, and JSON responses. Fake provider servers exercise rotation without network access. The optional Google SDK tests run when `google-genai` is installed.
