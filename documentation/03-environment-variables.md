# Environment Variables

This document is the complete reference for every environment variable consumed
by **meet-transcription**. Each variable is documented exactly as the code reads
it: the config dataclasses in
[`app/web/config.py`](../app/web/config.py),
[`app/worker/config.py`](../app/worker/config.py),
[`app/queue/config.py`](../app/queue/config.py), and
[`app/transcription/config.py`](../app/transcription/config.py), plus the Postgres
variables consumed by Docker Compose and the legacy CLI variables read by
`python -m app.main`.

For how these variables flow through the containers, see
[Architecture](01-architecture.md). For the canonical starter file, see
[`.env.example`](../.env.example).

## How configuration is loaded

`docker compose` reads a local `.env` file (copy it from `.env.example`) and uses
it for `${VAR}` substitution in `docker-compose.yml`. Inside the containers, each
service builds a frozen dataclass from `os.environ`:

| Dataclass | Module | Service(s) | Loader |
| --- | --- | --- | --- |
| `WebSettings` | `app/web/config.py` | web | `WebSettings.from_env()` |
| `WorkerSettings` | `app/worker/config.py` | worker | `WorkerSettings.from_env()` |
| `QueueSettings` | `app/queue/config.py` | web, worker | `QueueSettings.from_env()` |
| `TranscriptionConfig` | `app/transcription/config.py` | worker | `TranscriptionConfig.from_env()` |

```bash
# First-time setup
cp .env.example .env
# Edit .env (fill APP_SECRET_KEY, Google OAuth, passwords, …)
docker compose config        # validate substitution
docker compose build
docker compose up -d
```

### Defaults: code vs. docker-compose

Two defaults intentionally differ between the **code** and the shipped
`.env.example` / `docker-compose.yml`:

- `QUEUE_BACKEND` — the code default in `QueueSettings.from_env()` is `none`
  (so an un-configured worker keeps the legacy poll loop). The deployment default
  in `.env.example` is `redis`.
- `WORKER_POLL_INTERVAL_SECONDS` / `STALE_JOB_TIMEOUT_MINUTES` — the code
  defaults are `10` and `60`; `.env.example` ships `30` and `30`.

The **Required** column below means *required by the running service* (the web
service raises `ValueError` at startup if a required variable is missing/blank;
worker and transcription configs apply defaults and never crash on a missing or
bad value).

## Full reference table

### Database (PostgreSQL — single source of truth)

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `DATABASE_URL` | Yes | `postgresql+psycopg://meet_user:change_me@postgres:5432/meet_transcription` | web, worker, migrate | SQLAlchemy/psycopg URL for the single source of truth. Use host `postgres` inside Compose, `localhost` for a local server. **Must be a PostgreSQL URL — NEVER a SQLite path.** | Web/worker cannot start or read jobs; pointing at SQLite violates the architecture and breaks the repository layer. |
| `POSTGRES_DB` | Yes | `meet_transcription` | postgres | Database name created by the `postgres:16` container. Must match the db in `DATABASE_URL`. | Mismatch with `DATABASE_URL` → connection refused / missing tables. |
| `POSTGRES_USER` | Yes | `meet_user` | postgres | Role created by the `postgres` container. Must match the user in `DATABASE_URL`. | Mismatch → authentication failure for web/worker. |
| `POSTGRES_PASSWORD` | Yes | `change_me` | postgres | Password for `POSTGRES_USER`. Must match the password in `DATABASE_URL`. | Mismatch → authentication failure; weak value → exposed database. |

### Redis (queue + global execution lock)

Consumed by `QueueSettings` (`app/queue/config.py`). Valid backends:
`none`, `memory`, `redis`. An unknown `QUEUE_BACKEND` raises `ValueError`.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `QUEUE_BACKEND` | No (code default `none`; compose ships `redis`) | `redis` | web, worker | Selects the queue/lock backend: `redis` (production), `memory` (single-process dev), `none` (legacy poll loop, `claim_next_pending_job`). An unknown value raises `ValueError`. | `none` disables single-flight CPU serialization; an invalid value crashes startup. |
| `REDIS_URL` | No (default `redis://redis:6379/0`) | `redis://redis:6379/0` | web, worker | Connection URL for the Redis queue + global lock. Used only when `QUEUE_BACKEND=redis`. | Wrong host/port → enqueue/dequeue fails; UI shows the queue as offline. |
| `QUEUE_NAME` | No (default `transcription`) | `transcription` | web, worker | Logical queue name. Redis keys derive from it: `transcription:queue` (list), `transcription:queued` (dedupe set), `transcription:global_lock`. | Web and worker disagreeing on the name → jobs enqueued but never consumed. |
| `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS` | No (default `14400`) | `14400` | web, worker | TTL (seconds) of the `transcription:global_lock` (SET NX EX). Must exceed the longest expected transcription. Must be a positive integer or startup raises `ValueError`. | Too low → lock expires mid-job and a second job can start; non-positive/non-int → `ValueError`. |

### Web / Admin

Consumed by `WebSettings` (`app/web/config.py`). All four below (plus the Google
OAuth trio and `DATABASE_URL`) are **required**; a blank value raises
`Missing required environment variable: <KEY>`.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `ADMIN_USERNAME` | Yes | `admin` | web | Bootstrap admin login username for the UI. | Blank → web fails to start; weak value → easy admin takeover. |
| `ADMIN_PASSWORD` | Yes | `change-me` | web | Bootstrap admin login password. | Blank → web fails to start; weak/default value → admin takeover. |
| `APP_SECRET_KEY` | Yes | `a-long-random-string` | web, worker | Signs the session cookie **and** derives the Fernet key (`app/web/security.py`) used to encrypt Google tokens and per-user Deepgram keys at rest. The worker needs it to decrypt those credentials. | Blank → web fails to start; rotating it invalidates sessions and makes all encrypted tokens/keys undecryptable; leaking it exposes all secrets. |
| `SESSION_COOKIE_SECURE` | No (default `false`) | `true` | web | When `true`, sets the `Secure` flag on the session cookie (HTTPS-only). Parsed via `parse_bool`. | `false` behind HTTPS → cookie sent over plain HTTP if downgraded; `true` over plain HTTP in dev → you cannot stay logged in. |
| `TMP_DIR` | No (default `/app/tmp`) | `/app/tmp` | web, worker | Scratch directory. Web creates it at startup (`mkdir parents`); worker isolates each job under `TMP_DIR/jobs/<job_id>/` and cleans it up. | Unwritable/missing path → web startup failure or worker download/transcribe failures. |

### Google OAuth (web service)

Consumed by `WebSettings`. The requested scope is
`https://www.googleapis.com/auth/drive`. These power the per-user "Connect Google"
flow (`/connect-google` → `/oauth/google/callback`), **not** the legacy CLI.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `GOOGLE_WEB_CLIENT_ID` | Yes | `1234567890-abc.apps.googleusercontent.com` | web | OAuth 2.0 Web client ID from Google Cloud Console. | Blank → web fails to start; wrong value → OAuth `invalid_client`. |
| `GOOGLE_WEB_CLIENT_SECRET` | Yes | `GOCSPX-xxxxxxxxxxxx` | web | OAuth 2.0 Web client secret. | Blank → web fails to start; wrong value → token exchange fails. |
| `GOOGLE_REDIRECT_URI` | Yes | `http://localhost:8000/oauth/google/callback` | web | Exact OAuth callback URI. Must match an Authorized redirect URI in Google Cloud Console. Use `https://DOMAIN/oauth/google/callback` in production. | Mismatch with the console → `redirect_uri_mismatch`; users cannot connect Google. |

### Worker

Consumed by `WorkerSettings` (`app/worker/config.py`). The worker also reuses
`DATABASE_URL`, `APP_SECRET_KEY`, and `TMP_DIR`. Integer variables must be
positive; an invalid value raises `ValueError`.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `WORKER_REPOSITORY_BACKEND` | No (default `postgres`) | `postgres` | worker | Repository backend: `postgres` (production) or `memory` (dev/tests ONLY, **forbidden in production**). | `memory` in production → jobs vanish on restart (no persistence). |
| `WORKER_POLL_INTERVAL_SECONDS` | No (code default `10`; `.env.example` `30`) | `30` | worker | Seconds between poll/idle cycles (and re-queue of pending jobs while idle). Must be a positive integer. | Too high → slow pickup; non-positive/non-int → `ValueError`. |
| `WORKER_CONCURRENCY` | No (default `1`) | `1` | worker | Number of `run_queue_loop` threads when a queue is configured. Must be a positive integer. The global lock still serializes actual CPU transcription. | High values waste threads (lock serializes anyway); non-positive/non-int → `ValueError`. |
| `STALE_JOB_TIMEOUT_MINUTES` | No (code default `60`; `.env.example` `30`) | `30` | worker | Age after which a `processing` job is treated as stale and recovered (`reset_stale_processing_jobs`) at startup. Must be a positive integer. | Too low → jobs reset while still running; non-positive/non-int → `ValueError`. |
| `TMP_DIR` | No (default `/app/tmp`) | `/app/tmp` | web, worker | See Web/Admin row above. | See Web/Admin row above. |
| `DEEPGRAM_MODEL` | No (default `nova-3`) | `nova-3` | worker | Deepgram model used by the worker's Deepgram provider when local transcription is off. | Invalid model → Deepgram request rejected. |
| `DEEPGRAM_LANGUAGE` | No (default `pt-BR`) | `pt-BR` | worker | Deepgram language for the worker's Deepgram provider. | Wrong language → poor/incorrect transcript. |
| `DEEPGRAM_SMART_FORMAT` | No (default `true`) | `true` | worker | Deepgram `smart_format` toggle (worker provider). Parsed via `parse_bool`. | Disabling reduces formatting quality. |
| `DEEPGRAM_PUNCTUATE` | No (default `true`) | `true` | worker | Deepgram `punctuate` toggle (worker provider). | Disabling removes punctuation. |
| `DEEPGRAM_DIARIZE` | No (default `true`) | `true` | worker | Deepgram `diarize` toggle (worker provider). | Disabling removes speaker labels. |
| `DEEPGRAM_UTTERANCES` | No (default `true`) | `true` | worker | Deepgram `utterances` toggle (worker provider). | Disabling removes utterance segmentation. |

> Note: the worker's Deepgram API key is **per-user and encrypted** in Postgres;
> there is no `DEEPGRAM_API_KEY` env var in the web/worker deployment. The
> `DEEPGRAM_*` toggles above shape request options only.

### Local transcription (engine selection)

Consumed by `TranscriptionConfig` (`app/transcription/config.py`).
`from_env` **never raises on a bad value** — an unknown engine/model/compute just
makes the config invalid later (requiring Deepgram), so the worker never crashes
at startup. Validity is decided by `validate_local_config`, not here.

Provider selection rule
(`app/transcription/provider.py` + `app/transcription/factory.py`):

- `LOCAL_TRANSCRIPTION_ENABLED=false` → Deepgram; per-user key required.
- enabled **and** valid → local engine used; **no Deepgram key required**. UI
  shows `Modelo local ativo: <engine model compute/quant>`.
- enabled **but** invalid → Deepgram required; UI shows
  `Modelo local inválido. Consulte a documentação de modelos locais.` plus a link
  to `LOCAL_TRANSCRIPTION_DOC_URL`; `Run once` is blocked unless a Deepgram key is
  set. **No silent fallback.**

Allowed values (multilingual only — do **not** use `.en` models):

- Models (both engines): `tiny`, `base`, `small`, `medium`, `large-v1`,
  `large-v2`, `large-v3`, `large-v3-turbo`.
- Engines: `faster-whisper`, `whisper-cpp`.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `LOCAL_TRANSCRIPTION_ENABLED` | No (default `false`) | `false` | worker | Master switch for local CPU transcription. When `false`, the worker uses per-user Deepgram. Parsed via `parse_bool`; bad values fall back to the default. | `true` with an invalid config → all jobs need Deepgram; `false` when you intended local → Deepgram key required. |
| `LOCAL_TRANSCRIPTION_ENGINE` | No (default `faster-whisper`) | `faster-whisper` | worker | Local engine. Normalized (lowercased, `_`→`-`). Valid: `faster-whisper`, `whisper-cpp`. An invalid engine makes the config invalid (Deepgram required). | Unknown engine → local config invalid; Deepgram required. |
| `LOCAL_TRANSCRIPTION_MODEL` | No (default `small`) | `small` | worker | Model name (must be in the allowed multilingual list). | A `.en` or unknown model → config invalid; Deepgram required. |
| `LOCAL_TRANSCRIPTION_LANGUAGE` | No (default `auto`) | `pt` | worker | Transcription language: `auto`, `pt`, `en`, … | Wrong language hint → degraded transcript. |
| `LOCAL_TRANSCRIPTION_THREADS` | No (default `4`) | `4` | worker | CPU threads passed to the engine. Non-positive/non-int falls back to `4`. | Too high → CPU contention; too low → slow transcription. |
| `LOCAL_TRANSCRIPTION_MODEL_DIR` | No (default `/models`) | `/models` | worker | Directory where models live (mounted `./models:/models:ro`). `download_root` for faster-whisper. | Missing/empty dir without auto-download → model not found; config invalid. |
| `LOCAL_TRANSCRIPTION_DOC_URL` | No (default in code) | `https://github.com/gabedsam01/meet-transcription/blob/main/docs/architecture/local-transcription.md` | worker, web (link) | Docs URL surfaced in the UI when the local model is invalid. | Wrong URL → broken help link. |
| `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` | No (default `false`) | `false` | worker | Allow runtime model download. **Only faster-whisper** can auto-download (`local_files_only = not auto_download`). Parsed via `parse_bool`. | `true` in an offline/read-only env → download attempts fail; `false` without a pre-downloaded model → model not found. |

### faster-whisper-specific

Used only when `LOCAL_TRANSCRIPTION_ENGINE=faster-whisper`.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` | No (default `int8`) | `int8` | worker | CPU compute type for `WhisperModel`. Valid: `int8`, `int8_float32`, `float32`. | Invalid value → config invalid; Deepgram required. `float32` is slower/heavier. |

### whisper.cpp-specific

Used only when `LOCAL_TRANSCRIPTION_ENGINE=whisper-cpp`. The `whisper-cli` binary
is **external** (provide it via `WHISPER_CPP_BINARY`); it is not compiled into the
image.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `LOCAL_TRANSCRIPTION_QUANTIZATION` | No (default `q4_0`) | `q4_0` | worker | whisper.cpp quantization. Valid: `q4_0`, `q4_1`, `q5_0`, `q5_1`, `q8_0`. | Invalid value → config invalid; Deepgram required. |
| `LOCAL_TRANSCRIPTION_MODEL_PATH` | **Required for whisper-cpp** | `/models/ggml-small-q4_0.bin` | worker | Absolute path to the GGML model file. Always required when the engine is whisper-cpp (defaults to `None` if unset). | Missing/wrong path → model not found; config invalid; Deepgram required. |
| `WHISPER_CPP_BINARY` | Required for whisper-cpp | `/usr/local/bin/whisper-cli` | worker | Path to the external `whisper-cli` binary. Defaults to `None` if unset. | Missing/wrong path → `WhisperCppBinaryNotFoundError`; config invalid. |

### Build args (Docker build, NOT runtime)

These are `--build-arg` toggles in the `Dockerfile`; they are **not** read at
runtime by any config dataclass. All default to `false`.

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `INSTALL_LOCAL_TRANSCRIPTION` | No (default `false`) | `false` | image build | Umbrella build toggle for local transcription support. | Set without the engine-specific toggle → engine deps still missing. |
| `INSTALL_FASTER_WHISPER` | No (default `false`) | `true` | image build | `pip install faster-whisper` at build time. | `false` while enabling faster-whisper at runtime → import fails (`LocalTranscriptionUnavailable`). |
| `INSTALL_WHISPER_CPP` | No (default `false`) | `true` | image build | `apt-get install ffmpeg` at build time (audio extraction). The `whisper-cli` binary itself is **not** built — supply it via `WHISPER_CPP_BINARY`. | `false` while using whisper.cpp → ffmpeg missing; WAV extraction fails. |

```bash
# Example: build an image with faster-whisper baked in
docker build \
  --build-arg INSTALL_LOCAL_TRANSCRIPTION=true \
  --build-arg INSTALL_FASTER_WHISPER=true \
  -t ghcr.io/gabedsam01/meet-transcription:local .
```

### Observability, webhooks, and summaries

Consumed by the web and/or worker services. All optional with safe defaults, so
`docker compose config` works without setting any of them. See
[34-observability.md](34-observability.md), [35-webhooks.md](35-webhooks.md), and
[19-roadmap.md](19-roadmap.md).

| Name | Required | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `LOG_FORMAT` | No (default `text`) | `json` | web, worker | Log output format: `text` (human) or `json` (structured, one object/line). Secrets are redacted in both. | Unknown value falls back to `text`. |
| `APP_VERSION` | No | `0.2.0` | web | Version string surfaced by `GET /version`. | Blank → defaults to the built-in version. |
| `GIT_COMMIT` | No | `a1b2c3d` | web | Commit surfaced by `GET /version` (or `GIT_SHA`). | Blank → `unknown`. |
| `BUILD_TIME` | No | `2026-06-05T00:00:00Z` | web | Build timestamp surfaced by `GET /version`. | Blank → `null`. |
| `WEBHOOK_URL` | No (disabled if blank) | `https://hooks.example/meet` | worker | POST target for job events. Blank disables webhooks. | Unreachable URL → delivery fails (best-effort; the job is unaffected). |
| `WEBHOOK_EVENTS` | No (default `job.completed,job.failed`) | `job.failed` | worker | Comma-separated events to send. | Typo → that event is silently not sent. |
| `WEBHOOK_TIMEOUT_SECONDS` | No (default `10`) | `10` | worker | Per-request webhook timeout. | Too low → more timeouts/retries. |
| `WEBHOOK_MAX_RETRIES` | No (default `2`) | `2` | worker | Extra retries on transient failures (429/5xx/network). | `0` = deliver once, no retry. |
| `SUMMARY_ENABLED` | No (default `false`) | `false` | web | Meeting summaries toggle (roadmap; no LLM call yet; surfaced by `GET /version`). | Enabling has no effect until a provider ships. |
| `SUMMARY_PROVIDER` | No (default `none`) | `none` | web | Future summary provider name. | — |
| `SUMMARY_MODEL` | No | — | web | Future summary model name. | — |

### Legacy CLI only (`python -m app.main`)

These are consumed **only** by the legacy env-driven CLI worker
(`--once` / `--watch` / `--reprocess`). They are **not** used by the
web + worker + postgres architecture. In particular, the global
`DEEPGRAM_API_KEY` is read **only** here; the web UI uses per-user encrypted keys.

| Name | Required (legacy CLI) | Example | Service(s) | Description | Risk if wrong |
| --- | --- | --- | --- | --- | --- |
| `DEEPGRAM_API_KEY` | Yes | `your_deepgram_api_key` | legacy CLI | Deepgram API key for the legacy CLI only. **Never used by web/worker.** | Missing/invalid → legacy CLI cannot transcribe. **Do not commit.** |
| `GOOGLE_AUTH_MODE` | No (default `oauth`) | `oauth` | legacy CLI | Auth mode: `oauth` or service account. | Wrong mode → Drive auth fails. |
| `GOOGLE_OAUTH_CLIENT_SECRETS_FILE` | Conditional (oauth) | `/app/secrets/oauth-client.json` | legacy CLI | Path to the OAuth client secrets JSON. | Missing → OAuth flow fails. |
| `GOOGLE_OAUTH_TOKEN_FILE` | Conditional (oauth) | `/app/secrets/token.json` | legacy CLI | Path to the stored OAuth token. | Missing/expired → re-auth needed. |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Conditional (service account) | `/app/secrets/service-account.json` | legacy CLI | Path to the service-account JSON. | Missing → Drive auth fails. |
| `SOURCE_DRIVE_FOLDER_ID` | Yes | `your_source_drive_folder_id` | legacy CLI | Drive folder watched for input MP4s. | Wrong id → no recordings found. |
| `DESTINATION_DRIVE_FOLDER_ID` | No | `your_destination_drive_folder_id` | legacy CLI | Drive folder for the TXT backup copy. | Wrong id → backup upload fails/misplaced. |
| `STATE_FILE` | No | `/app/data/processed_files.json` | legacy CLI | JSON state file tracking processed files (git-ignored). | Wrong path → reprocessing or lost state. |
| `MAX_PROCESSING_ATTEMPTS` | No (default `2`) | `2` | legacy CLI | Max processing attempts per file. | Too low → fewer retries; too high → repeated failures. |
| `FAILED_RETRY_AFTER_SECONDS` | No (default `86400`) | `86400` | legacy CLI | Delay before retrying a failed file. | Too low → tight retry loops. |
| `DEEPGRAM_MODEL` | No (default `nova-3`) | `nova-3` | legacy CLI, worker | Deepgram model (shared name; see Worker row). | Invalid model → request rejected. |
| `DEEPGRAM_LANGUAGE` | No (default `pt-BR`) | `pt-BR` | legacy CLI, worker | Deepgram language (shared name). | Wrong language → poor transcript. |
| `DEEPGRAM_SMART_FORMAT` | No (default `true`) | `true` | legacy CLI, worker | Deepgram `smart_format` toggle. | Disabling reduces formatting. |
| `DEEPGRAM_PUNCTUATE` | No (default `true`) | `true` | legacy CLI, worker | Deepgram `punctuate` toggle. | Disabling removes punctuation. |
| `DEEPGRAM_DIARIZE` | No (default `true`) | `true` | legacy CLI, worker | Deepgram `diarize` toggle. | Disabling removes speaker labels. |
| `DEEPGRAM_UTTERANCES` | No (default `true`) | `true` | legacy CLI, worker | Deepgram `utterances` toggle. | Disabling removes utterance segmentation. |

## Validation checklist

Before deploying, verify your `.env` against the rules above:

```bash
# 1. Confirm Compose can substitute every variable
docker compose config

# 2. Confirm the image builds (with any local-transcription build args)
docker compose build

# 3. Run the test suite (uses dict-backed fakes — never SQLite)
python -m pytest -v

# 4. Byte-compile the app
python -m compileall app scripts
```

Common misconfigurations:

- **`DATABASE_URL` points at SQLite** — forbidden; PostgreSQL is the single
  source of truth.
- **`APP_SECRET_KEY` blank or rotated** — web won't start, or previously
  encrypted Google tokens / Deepgram keys become undecryptable.
- **`GOOGLE_REDIRECT_URI` mismatch** — `redirect_uri_mismatch` during OAuth.
- **`QUEUE_BACKEND=redis` but `REDIS_URL` unreachable** — UI shows the queue
  offline; jobs aren't consumed.
- **`LOCAL_TRANSCRIPTION_ENABLED=true` with an invalid model/engine/path** — no
  silent fallback; Deepgram becomes required and `Run once` is blocked without a
  key.
- **`WHISPER_CPP_BINARY` / `LOCAL_TRANSCRIPTION_MODEL_PATH` unset for
  whisper-cpp** — both are required; otherwise the local config is invalid.

## See also

- [Architecture](01-architecture.md)
- [`.env.example`](../.env.example)
- [`docker-compose.yml`](../docker-compose.yml)
