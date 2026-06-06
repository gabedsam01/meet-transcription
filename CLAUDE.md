# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## What this is

Meet Transcription watches a Google Drive folder for Google Meet recordings,
transcribes each MP4 — with **Deepgram** or a **local CPU engine**
(faster-whisper / whisper.cpp) — saves the transcript in **PostgreSQL**, and serves
a plain-text download from the web UI (optionally also uploading a copy to Drive).
It ships in two forms:

- a **web app** (FastAPI) for signing in, connecting Google, configuring folders,
  and triggering/inspecting jobs; and
- a **worker** that processes transcription jobs out of band.

## Architecture

Target architecture is **five services** (see `docker-compose.yml`):

- **postgres** — production database, the **single source of truth**.
- **redis** — transcription **queue** + **global execution lock** (NOT the main DB).
- **migrate** — one-shot `alembic upgrade head`; web/worker wait for it to finish.
- **web** — `uvicorn app.web.main:app` (HTTP, OAuth, UI). Code in `app/web/`.
- **worker** — `python -m app.worker.main` (DB-driven job processor). Code in `app/worker/`.

Web enqueues a `job_id` on "Run once"; the worker dequeues, takes the global lock,
claims the job in Postgres, and processes it under **provider-aware concurrency**:
cloud providers (Deepgram, I/O-bound) run several in parallel
(`CLOUD_TRANSCRIPTION_CONCURRENCY`, default 5) via a Redis semaphore; local CPU
engines run **one at a time** (`LOCAL_TRANSCRIPTION_CONCURRENCY`, default 1) via a
single Redis lock. Postgres stays authoritative: if Redis is lost, the worker
re-enqueues pending jobs on startup and while idle (`requeue_pending_jobs`). The
worker also runs an in-process **auto-poll** thread that scans each user's Drive
folder and enqueues new media (no sixth container). `QUEUE_BACKEND=none` poll mode
and the legacy CLI stay strictly one-at-a-time.

Transcription has a pluggable provider layer (`app/transcription/`): Deepgram
(per-user key) or a local CPU engine, selected by the rule in
`get_transcription_provider_status` — local when enabled **and** valid (no Deepgram
key needed), otherwise Deepgram is required (no silent fallback). Local engines are
**CPU-only** and off by default.

The **legacy worker** is the original env-driven CLI `python -m app.main`
(`--once` / `--watch` / `--reprocess`). It stores state in
`data/processed_files.json` and does not use the web database.

## Hard rules

1. **Do not break the legacy worker CLI** (`python -m app.main`). Its flags and
   behavior must keep working; it is still a supported deployment.
2. **PostgreSQL is the single source of truth — NO SQLite in the architecture.**
   The SQLAlchemy repository layer is the contract; the legacy `app/db.py` /
   `sqlite3` has been removed. New code depends on repository interfaces; tests
   use dict-backed in-memory fakes (never sqlite in-memory).
3. **Tokens and API keys are always encrypted at rest** (Fernet via
   `app/web/security.py`, key derived from `APP_SECRET_KEY`). Never store Google
   tokens or Deepgram keys in plaintext.
4. **Never run transcription inside an HTTP request.** Download/transcribe/upload
   must happen in the worker, never synchronously in a route handler. The request
   path only validates, creates a `pending` job, and enqueues its id to Redis.
5. **The UI does not use React** (or any SPA framework). It is server-rendered
   Jinja2 templates in `app/web/templates/` with local CSS in
   `app/web/static/styles.css`. No CDN assets, no build step.
6. **web and worker are separate services** sharing one image with different
   commands. Keep web (request/response) and worker (long-running jobs) concerns
   separate.
7. **Never commit secrets.** `.env`, `secrets/*.json`, `token.json`, and
   `data/processed_files.json` are git-ignored and must stay that way.
8. **Redis is the queue/locks/semaphore, NOT the source of truth.** Postgres is
   always the final defense against duplicates (`claim_job` is atomic). Anything
   Redis loses must be recoverable from Postgres (`requeue_pending_jobs`, gated by
   `next_retry_at`). Concurrency is **provider-aware**: cloud providers run a
   configurable number in parallel (default 5); local CPU runs 1. Token locks
   acquire with a unique token and release only if still owner (atomic Lua);
   the cloud semaphore acquires atomically via Lua. The worker default
   `QUEUE_BACKEND=none` keeps the legacy poll loop; `redis` is the production mode.
9. **Local transcription is CPU-only and off by default.** No GPU. Heavy engines
   (faster-whisper, whisper.cpp) are NOT installed in the base image — they are
   gated behind Docker build args, never installed at container startup. Providers
   import them lazily. Errors map to friendly `user_message`s (`app/errors.py`);
   tracebacks stay in logs, never in the UI.
10. **Run the tests before finishing.** See validation commands below.

## Conventions

- Long Drive ids and ISO timestamps must not blow out layout: ids render
  truncated/monospace (`mid`/`mono`), timestamps via the `dt` filter; full
  values live on the job detail page. Helpers are in `app/web/helpers.py`.
- DB access goes through the repository interfaces (`app/core/ports.py` for the
  worker view, `app/web/repositories.py` for the auth view) over the PostgreSQL
  adapters in `app/database/`, `app/db/postgres.py`, and
  `app/repositories/postgres.py`. UI job reads are user-scoped via the
  `JobRepository.get_job` / `list_jobs_for_user` contract methods.
- The `JobRepository` contract (`app/core/ports.py`) is: `claim_next_pending_job`,
  `claim_job`, `list_pending_jobs` (optional `now` gates `next_retry_at`),
  `create_job`, `get_job`, `mark_completed`, `mark_failed` (optional `error_code`),
  `schedule_retry`, `reset_job_for_retry`, `count_jobs_created_since`,
  `count_jobs_by_status`, `find_existing_job`, `reset_stale_processing_jobs`,
  `list_jobs_for_user`. `claim_job`/`list_pending_jobs` back the Redis path; keep
  all names and implement new ones in BOTH adapters (memory + postgres) and the
  `runtime_checkable` Protocol (update `tests/test_core_ports.py::_Stub`).
  Per-user auto-poll/guardrails live in `AutomationSettingsRepository`
  (`Repositories.automation`).
- The queue lives in `app/queue/` (`TranscriptionQueue` port + redis/memory
  adapters + `requeue_pending_jobs`). Beyond enqueue/dequeue it exposes
  provider slots (`acquire/release_provider_slot` — cloud semaphore / local lock),
  a generic `acquire/release_named_lock` (`lock:auto_poll`), and observability
  (`mark_processing`/`mark_dead`/`queue_stats`/`dead_job_ids`). The provider layer
  in `app/transcription/` (config, validation, normalizer, resolver,
  deepgram/faster-whisper/whisper.cpp providers, `provider_kind` for cloud/local
  classification). The normalized transcript schema is stored in
  `transcripts.transcript_json`; `transcripts.transcript_text` holds the TXT.

## Validation commands

```bash
python -m pytest -v
python -m compileall app scripts
docker compose config        # needs a local .env (cp .env.example .env)
docker compose build
```

## Scope note (multi-branch effort)

This codebase was built across branches forked from the same commit, now
integrated on `integration/postgres-platform`:

- `feat/ui-devops-polish` — UI, Docker, CI, docs.
- `feat/auth-users-settings` — auth, users/roles, per-user Google OAuth,
  **per-user encrypted Deepgram key (no env fallback)**, Drive settings by URL.
- `feat/postgres-core` — SQLAlchemy + PostgreSQL repositories and tables. The
  `JobRepository` contract names: `create_job`, `get_job`,
  `claim_next_pending_job`, `mark_completed`, `mark_failed`, `find_existing_job`,
  `reset_stale_processing_jobs`, `list_jobs_for_user`. Do not invent conflicting
  names.
- `feat/postgres-worker` — the `app.worker.main` job processor.

These concerns are integrated here; when modifying the database, per-user
Deepgram keys, or the worker, keep their contracts intact (method names,
encryption at rest, no SQLite).
