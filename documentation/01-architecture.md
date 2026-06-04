# Architecture

Meet Transcription watches a Google Drive folder for Google Meet recordings,
transcribes each MP4 (Deepgram **or** a local CPU engine), saves the transcript
in PostgreSQL, and serves a **Download TXT** from the web UI. Google Drive is the
input; an optional `.txt` backup copy can be uploaded to a destination Drive
folder.

This document describes the **container deployment** defined in
[`docker-compose.yml`](../docker-compose.yml): five services, their
responsibilities, the end-to-end job flow, and the design decisions that hold
the system together. A separate, env-driven legacy CLI exists for compatibility
(`python -m app.main`); it is **not** a compose service and is covered at the end.

---

## Service diagram

The five services share one network. `web` and `worker` run the **same image**
with different commands; `postgres` and `redis` are official images; `migrate` is
a one-shot job that reuses the application image.

```
                          ┌──────────────────────────────────────────────┐
                          │                  Browser                      │
                          │  login · dashboard · settings · jobs · TXT    │
                          └───────────────┬──────────────────────────────┘
                                          │ HTTP :8000
                                          ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │ web  (uvicorn app.web.main:app)                                       │
   │  • OAuth + UI + session auth     • validates & ENQUEUES jobs only     │
   │  • NEVER downloads/transcribes/uploads                                │
   └───────┬───────────────────────────────────────┬─────────────────────┘
           │ create pending job (SQL)               │ enqueue(job_id) (LPUSH)
           ▼                                         ▼
   ┌──────────────────────────┐            ┌──────────────────────────────┐
   │ postgres  (postgres:16)  │            │ redis  (redis:7-alpine)      │
   │  SINGLE SOURCE OF TRUTH  │            │  queue + global lock         │
   │  users, tokens, keys,    │            │  transcription:queue (list)  │
   │  drive settings, jobs,   │            │  transcription:queued (set)  │
   │  transcripts             │            │  transcription:global_lock   │
   └───────▲──────────────────┘            └──────────────┬───────────────┘
           │ claim_job() pending→processing               │ BRPOP job_id
           │ mark_completed / mark_failed                 │
   ┌───────┴──────────────────────────────────────────────┴──────────────┐
   │ worker  (python -m app.worker.main)                                   │
   │  • BRPOP → acquire global lock → claim_job → download MP4 from Drive  │
   │  • transcribe (Deepgram OR faster-whisper / whisper.cpp)              │
   │  • optional Drive TXT upload → store transcript → mark_completed      │
   └───────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────┐
   │ migrate  (one-shot)      │  alembic upgrade head, then exits 0.
   │  alembic upgrade head    │  web & worker wait for this to complete.
   └──────────────────────────┘
```

Volumes and mounts (from `docker-compose.yml`):

| Mount / volume                | web | worker | Purpose                                              |
| ----------------------------- | --- | ------ | ---------------------------------------------------- |
| `postgres_data` volume        |  —  |   —    | Postgres data dir; **this is the backup unit**.      |
| `redis_data` volume           |  —  |   —    | Redis persistence (queue/lock are rebuildable).      |
| `./models:/models:ro`         | yes |  yes   | Local model files (validated for UI status; loaded). |
| `./data:/app/data`            | yes |  yes   | Scratch/temp only — the database lives in Postgres.  |
| `./tmp:/app/tmp`              | yes |  yes   | Per-job scratch (`TMP_DIR`).                         |
| `./secrets:/app/secrets:ro`   | yes |  yes   | Read-only secrets (e.g. legacy service-account).     |

---

## Service responsibilities

### `postgres` (image `postgres:16`)

The single source of truth. Holds every durable fact: users, encrypted Google
tokens, encrypted per-user Deepgram keys, Drive settings, transcription jobs, and
the transcripts themselves. Has a healthcheck (`pg_isready`) and a named volume
`postgres_data`. Reached over `DATABASE_URL` (`postgresql+psycopg://…@postgres:5432/…`)
— **never SQLite**.

### `redis` (image `redis:7-alpine`)

The transcription **queue** and the single global **execution lock**. It is a
wake-up signal and a concurrency guard, not a database. Has a healthcheck
(`redis-cli ping`) and a `redis_data` volume. Used only when `QUEUE_BACKEND=redis`
(the compose default).

### `migrate` (one-shot)

Runs `alembic upgrade head` against Postgres and exits. `restart: "no"` — it must
not be restarted after a clean exit. It reuses the application image (alembic and
`alembic.ini` are baked in) and the same `DATABASE_URL`. `web` and `worker` wait
for it to complete successfully, so the schema is always current before any
request is served or any job is claimed.

### `web` (`uvicorn app.web.main:app --host 0.0.0.0 --port 8000`)

Serves the UI and the OAuth flow on port `8000`. Server-rendered Jinja2 templates
(`app/web/templates/`) with local CSS (`app/web/static/styles.css`) — **no React,
no SPA, no build step**. Responsibilities:

- Session-cookie auth (`require_user` / `require_admin`).
- Google OAuth connect/callback (scope `https://www.googleapis.com/auth/drive`).
- Settings: Drive folders (by URL), per-user Deepgram key (encrypted), admin users.
- **Run once** that *validates and creates a pending job, then enqueues its id* —
  and nothing more.
- Reads jobs/transcripts (user-scoped) for the dashboard, jobs list, job detail,
  and **Download TXT**.

The web service uses **per-user encrypted Deepgram keys**, so it needs **no global
`DEEPGRAM_API_KEY`**. It **never** downloads, transcribes, or uploads.

### `worker` (`python -m app.worker.main`)

Owns all processing. On startup it recovers stale `processing` jobs, then (in
queue mode) reconciles pending Postgres jobs into Redis and runs `WORKER_CONCURRENCY`
consumer threads. For each job it claims it, downloads the MP4 from Drive,
transcribes, optionally uploads a TXT backup to Drive, stores the transcript, and
marks the job completed (or failed with a friendly, secret-free message).

---

## Startup order

`depends_on` conditions in `docker-compose.yml` enforce this exact sequence:

```
postgres  ──(healthcheck: pg_isready)──▶ healthy
redis     ──(healthcheck: redis-cli ping)──▶ healthy
                         │
                         ▼
migrate   ── alembic upgrade head ──▶ exits 0 (service_completed_successfully)
                         │
                         ▼
web   +  worker   start  (both wait on: postgres healthy + redis healthy + migrate completed)
```

Concretely, `web` and `worker` each declare:

```yaml
depends_on:
  postgres:
    condition: service_healthy
  redis:
    condition: service_healthy
  migrate:
    condition: service_completed_successfully
```

Bring it up:

```bash
cp .env.example .env          # populate secrets for production
docker compose up --build     # local: builds from ./Dockerfile
# or, in production:
docker compose pull && docker compose up -d   # pulls ghcr.io/gabedsam01/meet-transcription:latest
```

---

## End-to-end job flow

The happy path from a Drive recording to a downloadable transcript:

1. **Sign in & connect.** The user logs in (`/login`), connects Google
   (`/connect-google` → `/oauth/google/callback`, scope
   `https://www.googleapis.com/auth/drive`), sets the source Drive folder (and
   optional destination) under `/settings/drive`, and — unless a valid local
   engine is active — saves a Deepgram key under `/settings/deepgram`.

2. **Run once (enqueue only).** The user clicks **Run once** (`POST /jobs/run-once`).
   The web layer calls `create_next_pending_job(...)`, which validates settings,
   token, and (when required) the Deepgram key, finds the next un-transcribed
   Drive video, and creates a row in `transcription_jobs` with `status=pending`
   and `source_file_id` set. It returns a `JobCreationResult`; the route maps its
   status to a Portuguese flash message (`RUN_ONCE_MESSAGES`). If a job was
   `created`, the route best-effort calls `app.state.queue.enqueue(job.id)` (Redis
   `LPUSH` onto `transcription:queue` + add to the `transcription:queued` dedupe
   set). **No transcription happens in this request.**

   > If Redis is down, the job simply stays `pending` in Postgres and the UI shows
   > "Fila indisponível no momento…"; the worker reconciles it later.

3. **Worker dequeues.** A worker consumer thread `BRPOP`s the next `job_id` from
   the tail of `transcription:queue` (FIFO).

4. **Global lock.** The worker acquires `transcription:global_lock`
   (`SET NX EX` with a token, TTL `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS`, default
   `14400`s = 4h). Only one transcription runs at a time across the whole VPS.

5. **Atomic claim (final dedupe).** The worker calls `claim_job(job_id)`, which
   atomically transitions the row `pending → processing` in Postgres and sets
   `started_at`. This is the authoritative dedupe defense: even if a `job_id`
   slipped into the queue twice, only the first claim wins.

6. **Resolve provider.** Based on `LOCAL_TRANSCRIPTION_ENABLED` and validation:
   local engine if enabled-and-valid, otherwise Deepgram with the user's key. No
   silent fallback (see [Provider rule](#provider-rule-local-vs-deepgram)).

7. **Download & transcribe.** The worker downloads the MP4 from Drive into an
   isolated scratch dir `TMP_DIR/jobs/<job_id>/`, then transcribes:
   - **Deepgram** (`deepgram_provider.py`) — wraps the existing client, keeps the
     legacy `.txt` format.
   - **faster-whisper** (`faster_whisper_provider.py`) — CPU `WhisperModel`,
     `compute_type`, `cpu_threads`, `download_root=model_dir`.
   - **whisper.cpp** (`whisper_cpp_provider.py`) — extract 16 kHz mono WAV via
     `ffmpeg`, run `whisper-cli -oj`, parse offsets.

8. **Store results.** Optionally upload a TXT copy to the destination Drive
   folder (**only** if `save_copy_to_drive` is on **and** a destination is set),
   then write a `transcripts` row (`transcript_text` + normalized
   `transcript_json`) and `mark_completed`. Any failure → `mark_failed` with a
   friendly `user_message` (never a traceback). Scratch is always cleaned up.

9. **Download TXT.** The user returns to `/jobs`, sees the job `completed`, and
   clicks **Download TXT** (`GET /jobs/{id}/download`), which serves
   `transcripts.transcript_text` as an attachment. Reads are strictly user-scoped:
   another user's (or unknown) `job_id` returns 404.

### Worker startup & self-healing

`app.worker.main.run()` does, in order:

1. `recover_stale_jobs(...)` → `reset_stale_processing_jobs(...)` so jobs stuck in
   `processing` past `STALE_JOB_TIMEOUT_MINUTES` are reset/failed.
2. If `container.queue is not None` (queue mode): `requeue_pending_jobs(...)`
   re-enqueues every Postgres `pending` job that is not already queued (uses
   `ensure_queued`, which self-heals an id orphaned in the dedupe set via Redis
   `LPOS`), then starts `WORKER_CONCURRENCY` `run_queue_loop` threads.
3. Otherwise (`QUEUE_BACKEND=none`): start `run_worker_loop` threads that poll
   `claim_next_pending_job` directly from Postgres.

This is why an enqueue can be best-effort: **Postgres is authoritative**, and the
worker rebuilds the queue from it on startup and while idle.

---

## Why Postgres is the single source of truth

- **One authoritative store.** Every durable fact — users, encrypted tokens,
  encrypted Deepgram keys, Drive settings, jobs, and transcripts — lives in
  Postgres. There is **no SQLite mode anywhere** in the architecture; new code
  depends on the repository interfaces (`app/core/ports.py`), and the Postgres
  adapter (`app/repositories/postgres.py`) is the production implementation.
- **Atomicity guards correctness.** `claim_job` performs the `pending → processing`
  transition atomically, which is the real dedupe and concurrency boundary. A
  partial unique index (`uq_transcription_jobs_completed_source`) prevents two
  *completed* jobs for the same source file.
- **Queue/Redis is disposable.** If Redis is wiped, no transcript or job is lost;
  `requeue_pending_jobs` rebuilds the queue from Postgres. The reverse is not
  true, which is exactly why Postgres — not Redis — is the source of truth.
- **One backup unit.** Operationally, backing up the `postgres_data` volume backs
  up the whole system state.

See [Database & Repositories](10-postgres-and-migrations.md) for the schema and contract.

## Why Redis is the queue/lock — not the database

Redis is the **wake-up signal** ("which job to look at next") and the
**cross-process concurrency control** ("process one transcription at a time on a
CPU-bound VPS"). It deliberately holds no durable truth:

| Redis key                    | Type   | Role                                                            |
| ---------------------------- | ------ | -------------------------------------------------------------- |
| `transcription:queue`        | list   | `LPUSH` at head, `BRPOP` at tail → FIFO of pending `job_id`s.   |
| `transcription:queued`       | set    | Dedupe — avoids enqueuing the same id twice.                   |
| `transcription:global_lock`  | string | `SET NX EX` with a token; the single global execution lock.    |

- **FIFO ordering & blocking consume.** `BRPOP` lets the worker sleep until a job
  arrives instead of busy-polling Postgres.
- **A real cross-process lock.** `SET NX EX` with a TTL of
  `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS` (default `14400`) ensures only one
  CPU-bound transcription runs at a time, even with multiple worker threads or
  several "Run once" clicks at once. A crash can't deadlock the system because the
  lock expires.
- **Everything is rebuildable.** The queue and dedupe set are reconstructed from
  Postgres `pending` jobs by `requeue_pending_jobs`, so losing Redis costs a
  wake-up, not data. That is why Redis is a queue/lock, not the DB.
- **Optional.** `QUEUE_BACKEND` can be `redis` (compose default), `memory`
  (dev/tests), or `none`. With `none`, `build_queue` returns `None` and the worker
  falls back to the legacy poll loop (`claim_next_pending_job`); the system still
  works, just without push-style wake-ups.

## Why the worker is a separate service

- **Separation of concerns.** `web` is request/response (must answer in
  milliseconds); `worker` runs long, CPU- or network-bound jobs (download →
  transcribe → upload). Mixing them would block the event loop and the UI.
- **Independent scaling & lifecycle.** They share one image but run different
  commands and have different env (`WORKER_CONCURRENCY`,
  `WORKER_POLL_INTERVAL_SECONDS`, `STALE_JOB_TIMEOUT_MINUTES`,
  `LOCAL_TRANSCRIPTION_*`). The worker can be restarted, scaled, or given more
  CPU without touching the web tier.
- **Resilience.** If the worker is down, `web` still serves the UI and enqueues
  jobs; they stay `pending` in Postgres and are reconciled when the worker comes
  back. If the web is down, in-flight jobs keep processing.

## Why the web UI never processes transcriptions

This is a hard architectural rule: **never run transcription inside an HTTP
request.** The request path only validates and enqueues.

- **`POST /jobs/run-once` does only two things:** call `create_next_pending_job`
  (which validates + inserts a `pending` row) and best-effort `queue.enqueue(id)`.
  Download/Deepgram/local-engine/upload all happen in the worker.
- **Why it matters:** a transcription can take minutes and pegs a CPU; doing it
  in-request would block the worker thread serving HTTP, risk timeouts, and let a
  single click monopolize the box. The global lock and single-job-at-a-time
  policy also can't be enforced from independent web requests.
- **What the UI reads, not runs:** the dashboard shows status cards (Google, Drive
  source, Deepgram, Transcription = local model status, Queue = Redis
  online/offline/poll, Total jobs, Last job); the jobs page shows status badges,
  friendly errors, **Download TXT** when completed, and warnings when the local
  model is invalid or the queue is unavailable. All read-only.

---

## Provider rule (local vs Deepgram)

Resolved by `app/transcription/provider.py` (`get_transcription_provider_status`)
and `app/transcription/factory.py` (`resolve_provider`). **There is no silent
fallback.**

| `LOCAL_TRANSCRIPTION_ENABLED` | Local model | Effect                                                                                                   |
| ----------------------------- | ----------- | -------------------------------------------------------------------------------------------------------- |
| `false`                       | —           | **Deepgram**; a per-user key is required.                                                                 |
| `true`                        | valid       | **Local engine** is used; **no Deepgram key required**. UI: "Modelo local ativo: `<engine model compute/quant>`". |
| `true`                        | invalid     | Deepgram required; UI shows "Modelo local inválido. Consulte a documentação de modelos locais." + a link to `LOCAL_TRANSCRIPTION_DOC_URL`; **Run once is blocked** unless a Deepgram key is set. |

`run-once` passes `deepgram_required=transcription_status.deepgram_required` into
`create_next_pending_job`, so a valid local engine drops the key requirement.

---

## Configuration reference

Every value has a safe default so `docker compose config` / `build` work without a
populated `.env`; **production must override the secrets**.

### PostgreSQL

| Variable            | Notes                                                                 |
| ------------------- | --------------------------------------------------------------------- |
| `POSTGRES_DB`       | Database name.                                                        |
| `POSTGRES_USER`     | Database user.                                                        |
| `POSTGRES_PASSWORD` | Database password (override in production).                           |
| `DATABASE_URL`      | `postgresql+psycopg://user:pass@postgres:5432/db` — **never sqlite**. |

### Redis / queue

| Variable                                | Default (compose) | Notes                                          |
| --------------------------------------- | ----------------- | ---------------------------------------------- |
| `QUEUE_BACKEND`                         | `redis`           | `redis` \| `memory` \| `none`. Code default = `none`. |
| `REDIS_URL`                             | `redis://redis:6379/0` |                                           |
| `QUEUE_NAME`                            | `transcription`   | Key prefix for the queue/set/lock.             |
| `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS` | `14400`           | Global lock TTL (4h).                          |

### Web / admin / OAuth

| Variable                                  | Notes                                                         |
| ----------------------------------------- | ------------------------------------------------------------ |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD`        | Bootstrap admin (ensured on startup).                        |
| `APP_SECRET_KEY`                          | Session secret **and** the Fernet encryption key.            |
| `SESSION_COOKIE_SECURE`                   | `true` behind HTTPS.                                         |
| `GOOGLE_WEB_CLIENT_ID` / `_SECRET`        | OAuth web client.                                            |
| `GOOGLE_REDIRECT_URI`                     | e.g. `http://localhost:8000/oauth/google/callback`.          |

OAuth scope requested: `https://www.googleapis.com/auth/drive`.

### Worker

| Variable                       | Default | Notes                                          |
| ------------------------------ | ------- | ---------------------------------------------- |
| `WORKER_REPOSITORY_BACKEND`    | `postgres` | `postgres` \| `memory` (dev/tests only).    |
| `WORKER_POLL_INTERVAL_SECONDS` | `30`    | Poll cadence (poll mode / idle).               |
| `WORKER_CONCURRENCY`           | `1`     | Consumer threads.                              |
| `STALE_JOB_TIMEOUT_MINUTES`    | `30`    | Recovery threshold for stuck `processing`.     |
| `TMP_DIR`                      | `/app/tmp` | Per-job scratch root (`TMP_DIR/jobs/<id>/`). |

### Local transcription (runtime)

`LOCAL_TRANSCRIPTION_ENABLED` (default `false`), `LOCAL_TRANSCRIPTION_ENGINE`
(`faster-whisper` | `whisper-cpp`), `LOCAL_TRANSCRIPTION_MODEL`,
`LOCAL_TRANSCRIPTION_LANGUAGE` (`auto`|`pt`|`en`|…), `LOCAL_TRANSCRIPTION_THREADS`,
`LOCAL_TRANSCRIPTION_MODEL_DIR` (`/models`), `LOCAL_TRANSCRIPTION_COMPUTE_TYPE`
(`int8`|`int8_float32`|`float32`; faster-whisper), `LOCAL_TRANSCRIPTION_QUANTIZATION`
(`q4_0`|`q4_1`|`q5_0`|`q5_1`|`q8_0`; whisper.cpp), `LOCAL_TRANSCRIPTION_MODEL_PATH`
(whisper.cpp, **always required**), `WHISPER_CPP_BINARY` (path to `whisper-cli`),
`LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` (default `false`; only faster-whisper can
auto-download), `LOCAL_TRANSCRIPTION_DOC_URL`.

Models (both engines, **multilingual only — do not use `.en`**): `tiny`, `base`,
`small`, `medium`, `large-v1`, `large-v2`, `large-v3`, `large-v3-turbo`.

### Build args (Docker, **not** runtime)

`INSTALL_LOCAL_TRANSCRIPTION`, `INSTALL_FASTER_WHISPER`, `INSTALL_WHISPER_CPP`
(all default `false`). `INSTALL_FASTER_WHISPER` pip-installs `faster-whisper`;
`INSTALL_WHISPER_CPP` apt-installs `ffmpeg`. The `whisper-cli` binary itself is
**external** (`WHISPER_CPP_BINARY`), not compiled into the image.

---

## Legacy CLI (compatibility only — not a compose service)

`python -m app.main` (`--once` / `--watch` / `--reprocess`) is the original
env-driven worker. It uses a mounted `token.json` or a service account, stores
state in `data/processed_files.json`, and reads the **global `DEEPGRAM_API_KEY`**
(`GOOGLE_AUTH_MODE`, `GOOGLE_OAUTH_*`, `SOURCE_DRIVE_FOLDER_ID`,
`DESTINATION_DRIVE_FOLDER_ID`, `STATE_FILE`, …). It does **not** use the web
database. It is supported and must keep working, but the container deployment
(web + worker) uses **per-user encrypted Deepgram keys** and does **not** read the
global `DEEPGRAM_API_KEY`.

---

## See also

- [Architecture](01-architecture.md) (this document)
- [Database & Repositories](10-postgres-and-migrations.md)
