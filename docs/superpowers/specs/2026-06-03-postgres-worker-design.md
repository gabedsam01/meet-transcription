# Design: PostgreSQL Multiuser Worker

Branch: `feat/postgres-worker`

## Goal

Deliver the new multiuser transcription worker. The UI creates `pending` jobs,
a standalone worker process claims and processes them concurrently and safely,
transcription runs through Deepgram using each user's own API key, and the
resulting transcript is stored in PostgreSQL so the UI can offer a download.

This branch delivers the **worker flow, job creation service, download service,
repository ports (contracts), and in-memory fakes**. It does **not** deliver the
real PostgreSQL infrastructure, models, or repositories — those belong to
`feat/postgres-core`. User/settings persistence and OAuth/settings repointing
plus encryption belong to `feat/auth-users-settings`.

## Parallel-terminal ownership

This work is one of several coordinated branches that will merge into `dev`:

- **`feat/postgres-core`** — PostgreSQL engine/session, schema/migrations, and
  the real repository adapters (`app/repositories/postgres.py`). Owns the `db`
  Postgres service in docker-compose, `SQLAlchemy`/`psycopg` dependencies, and
  the DDL for the new columns and tables described here.
- **`feat/auth-users-settings`** — users/settings persistence, OAuth/settings
  routes repointed to PostgreSQL, and Fernet encryption-at-rest of
  `refresh_token`, `client_secret`, and the per-user Deepgram key.
- **`feat/postgres-worker`** (this branch) — ports + fakes + worker + job
  service + download service.
- **`feat/ui-devops-polish`** — broader UI/devops.

Because the other branches are not yet delivered, this branch builds strictly
against **ports** (Protocols) and tests against **in-memory fakes**. Real
PostgreSQL integration happens after `feat/postgres-core` is merged.

## Architecture

Ports and adapters (hexagonal). New code depends only on framework-agnostic
**domain dataclasses** and **repository ports**. No `SQLAlchemy`, no `psycopg`,
no `sqlite3` in this branch's new code.

```
app/
  core/
    models.py          # Job, Settings, GoogleToken, Transcript dataclasses + JobStatus
    ports.py           # Repository Protocols + contracts (SKIP LOCKED / dedup / stale)
  repositories/
    __init__.py        # build_repositories(backend) factory + backend selection rules
    memory.py          # in-memory adapters: test fakes + dev/smoke backend (pure dicts)
    # postgres.py      <- delivered by feat/postgres-core
  services/
    job_service.py     # create_next_pending_job(user_id)
    download_service.py# get_downloadable_transcript(job_id, requester_user_id, is_admin)
  google_auth.py       # build_oauth_credentials(...) shared by web + worker
  worker/
    __init__.py
    config.py          # WorkerSettings.from_env()
    container.py       # wires repositories + clients for the selected backend
    processor.py       # JobProcessor.process(job)
    loop.py            # run_worker_loop(stop_event)
    main.py            # python -m app.worker.main
    locks.py           # per-job workspace + stale helpers (only if needed)
```

Two adapters implement every port: the shipped **in-memory** adapter
(`app/repositories/memory.py`, doubling as test fakes and a dev/smoke backend)
and the **PostgreSQL** adapter delivered later by `feat/postgres-core`.

## Domain models

Plain dataclasses, the boundary between the worker and any database.

- `Job`: `id`, `user_id`, `source_file_id`, `source_file_name`, `status`,
  `error_message`, `attempts`, `created_at`, `updated_at`, `started_at`,
  `processed_at`, `transcript_drive_file_id`.
- `Settings`: `user_id`, `source_drive_folder_id`, `destination_drive_folder_id`,
  `poll_interval_seconds`, `save_copy_to_drive` (bool), `deepgram_api_key`.
- `GoogleToken`: `access_token`, `refresh_token`, `token_uri`, `client_id`,
  `client_secret`, `scopes`, `expiry`.
- `Transcript`: `id`, `job_id`, `user_id`, `text`, `json_payload` (JSONB-ready
  dict), `drive_file_id` (nullable), `created_at`.
- `JobStatus`: `pending | processing | completed | failed | skipped`.

Repositories return **decrypted, ready-to-use** domain objects. Encryption at
rest (Fernet via `APP_SECRET_KEY`, the scheme already in `app/web/security.py`)
is the adapter's responsibility, owned by `feat/auth-users-settings`. The worker
never sees ciphertext and never holds a cipher.

## Repository ports

Defined as `typing.Protocol` in `app/core/ports.py`. The PostgreSQL and
in-memory adapters both implement them.

- `JobRepository`
  - `claim_next_pending_job(worker_id, now) -> Job | None`
  - `create_job(user_id, source_file_id, source_file_name, now) -> Job`
  - `get_job(job_id) -> Job | None`
  - `mark_completed(job_id, now, transcript_drive_file_id=None) -> None`
  - `mark_failed(job_id, error_message, now) -> None`
  - `find_existing_job(user_id, source_file_id, statuses) -> Job | None`
  - `completed_source_file_ids(user_id) -> set[str]`
  - `reset_stale_processing_jobs(stale_before, now) -> list[Job]`
  - `list_jobs_for_user(user_id) -> list[Job]`
- `TranscriptRepository`
  - `create(job_id, user_id, text, json_payload, drive_file_id, now) -> Transcript`
  - `get_by_job(job_id) -> Transcript | None`
- `SettingsRepository`
  - `get(user_id) -> Settings | None`
- `GoogleTokenRepository`
  - `get(user_id) -> GoogleToken | None`

### Locking contract (satisfies requirement 2)

`claim_next_pending_job` MUST atomically hand a job to exactly one worker. The
PostgreSQL adapter implements it as:

```sql
BEGIN;
SELECT * FROM transcription_jobs
 WHERE status = 'pending'
 ORDER BY created_at, id
 FOR UPDATE SKIP LOCKED
 LIMIT 1;                       -- none -> return None
UPDATE transcription_jobs
   SET status = 'processing',
       attempts = attempts + 1,
       started_at = :now,
       updated_at = :now
 WHERE id = :id;
COMMIT;                         -- download/transcribe happen OUTSIDE this tx
```

The transaction is short: select, mark `processing`, increment `attempts`,
commit. Download and transcription happen **outside** it. Completion/failure are
separate short writes (`mark_completed` / `mark_failed`). The in-memory adapter
reproduces the same atomic-claim guarantee with a `threading.Lock`, so
concurrency tests are deterministic without a database.

### Dedup contract (requirement 3)

A new job is created only when no job already exists for the
`(user_id, source_file_id)` pair in status `pending`, `processing`, or
`completed`. `find_existing_job` plus `completed_source_file_ids` support this.

### Stale contract (requirement 6)

`reset_stale_processing_jobs(stale_before, now)` finds jobs in `processing`
whose `started_at` (or `updated_at` if null) is older than `stale_before`, marks
them `failed` with a `"stale timeout"` message, and returns them. Called once at
worker startup.

## Backend selection rules (mandatory)

`WORKER_REPOSITORY_BACKEND` selects the adapter:

- **Unset → default is `postgres`.** Production default is always PostgreSQL.
- `postgres` → use `app/repositories/postgres.py`. If that module is not yet
  present (i.e. `feat/postgres-core` not integrated), fail fast with a clear,
  actionable error: integrate/merge `feat/postgres-core` before running the
  worker against PostgreSQL.
- `memory` → in-memory adapter. **Forbidden in production.** It is non-persistent
  and exists only for tests, local smoke runs, and development. This restriction
  is documented in `.env.example` and the README worker section.

## Worker flow (`python -m app.worker.main`)

1. Load `WorkerSettings.from_env()`, build the `container` (repositories for the
   selected backend; `postgres` default).
2. **Stale recovery:** `reset_stale_processing_jobs(now - STALE_JOB_TIMEOUT_MINUTES)`.
3. Spawn `WORKER_CONCURRENCY` loop threads (default 1). Each loop:
   `claim_next_pending_job` → if `None`, sleep `WORKER_POLL_INTERVAL_SECONDS`;
   else process the job.
4. `JobProcessor.process(job)`:
   - load `Settings` and `GoogleToken` (decrypted) and the per-user Deepgram key
     (per-user only — fail the job with a clear message if absent);
   - build `DriveClient.from_credentials(...)` and
     `DeepgramClient(api_key=user_key, ...global transcription options)`;
   - create per-job workspace `TMP_DIR/jobs/<job_id>/` and download the job's
     `source_file_id` MP4 into it;
   - transcribe; `transcript_text = format_transcript(...)`;
     `transcript_json = raw Deepgram JSON`;
   - `TranscriptRepository.create(...)`;
   - if `save_copy_to_drive` and `destination_drive_folder_id` is set, upload the
     TXT to Drive and record `transcript_drive_file_id`;
   - `mark_completed`;
   - `finally`: remove **only** `TMP_DIR/jobs/<job_id>/` (never `TMP_DIR`, never
     another job's directory).
   - On any exception: `mark_failed(job_id, readable_message)`, full traceback to
     logs only; the job is never left in `processing`.
5. Graceful shutdown on SIGINT/SIGTERM via a shared stop `Event`.

## Job creation service (requirement 3)

`job_service.create_next_pending_job(repositories, drive_factory, user_id)`:

1. Load the user's `Settings`; if source folder missing → `no_settings`.
2. Load the user's `GoogleToken`; if missing → `not_connected`.
3. Build a Drive client and list source-folder videos.
4. Choose the first video whose `source_file_id` has no existing
   `pending|processing|completed` job for the user (dedup contract).
5. Create a `pending` job carrying `source_file_id` + `source_file_name`.
6. Return a typed result: `created` (with job), `no_settings`, `not_connected`,
   or `no_new_videos`.

Called by `POST /jobs/run-once`. The route **only** creates the pending job and
returns; the standalone worker performs the heavy work. The previous in-process
`BackgroundTasks` path is removed.

## Download service and route (requirement 7)

`download_service.get_downloadable_transcript(repositories, job_id, requester_user_id, is_admin)`:

- requester must own the job, or be admin;
- job must be `completed`;
- a transcript must exist;
- returns `(filename, text)` with `filename = f"{sanitize_filename(name)}_Transcricao.txt"`.

`GET /jobs/{job_id}/download` returns `text/plain` as an attachment with the
sanitized filename. Admin status is derived in the route from the session
(`session_email == ADMIN_USERNAME`) and passed in. Distinct failures map to
`404` (no job / not found for user) and `409`/`400` (not completed / no
transcript) as appropriate; a stranger requesting another user's job is treated
as not found.

## Client adaptations (small, non-breaking)

- `DeepgramClient`: already stores `api_key` per instance. Add a per-call
  `api_key` override and a `from_api_key(api_key, ...)` classmethod. Keep
  `from_settings` for the CLI. No global key in the new flow.
- `DriveClient`: keep existing `from_credentials`. Add `download_by_id(file_id,
  destination)` so the worker can fetch a job's source file directly. Old methods
  untouched.

## CLI compatibility (requirement 11)

`app/main.py` (`--once`, `--watch`, `--once --reprocess <id>`), `app/processor.py`
(the simple worker), and `app/state.py` (JSON state) are **untouched**. The new
worker lives entirely under `app/worker/` and does not import the JSON state or
the env-only `Settings`.

## Minimal UI changes (requirement 8)

- A **Download** button on `completed` jobs (links to `/jobs/{id}/download`).
- A **Drive** link when `transcript_drive_file_id` is set.
- No layout redesign.

## Environment variables

New:

- `WORKER_REPOSITORY_BACKEND` — `postgres` (default) or `memory` (dev/test only,
  forbidden in production).
- `WORKER_POLL_INTERVAL_SECONDS` — default `10`.
- `WORKER_CONCURRENCY` — default `1`.
- `STALE_JOB_TIMEOUT_MINUTES` — default `60`.
- `DATABASE_URL` — PostgreSQL DSN, consumed by the `postgres` adapter delivered
  by `feat/postgres-core`.

Reused for the worker's Deepgram transcription **options** (not the key):
`DEEPGRAM_MODEL`, `DEEPGRAM_LANGUAGE`, `DEEPGRAM_SMART_FORMAT`,
`DEEPGRAM_PUNCTUATE`, `DEEPGRAM_DIARIZE`, `DEEPGRAM_UTTERANCES`. The Deepgram
**key is per-user** and read from the DB, never from env, in the new flow.

## Docker compose

Add a `transcription-worker` service running `python -m app.worker.main`. This
branch does **not** add the `db` Postgres service, `SQLAlchemy`/`psycopg`
dependencies, or `depends_on: db` wiring — that belongs to `feat/postgres-core`.
`docker compose config` and `docker compose build` remain valid. The integration
note documents the `db` service + DSN + `depends_on` that `feat/postgres-core`
will add.

## Testing (requirement 12)

All tests run against the in-memory adapter; no database required.

- Job creation does not duplicate a `completed` job.
- Job creation does not create a new job when `pending`/`processing`/`completed`
  already exists for `(user, source_file_id)`.
- Job creation picks the next not-completed video.
- Worker claim flips `pending` → `processing` and increments `attempts`.
- Worker completion creates a transcript and marks the job `completed`.
- Worker marks the job `failed` with `error_message` on exception, never leaving
  it `processing`.
- Cleanup removes only the `<job_id>` workspace.
- Download enforces ownership: owner ok, stranger denied, admin ok, not-completed
  denied, missing transcript denied.
- `DeepgramClient` accepts a per-user `api_key`.
- Stale recovery marks old `processing` jobs `failed` at startup.

Existing tests that assert the old `BackgroundTasks` run-once behavior
(`tests/test_web_routes.py`, `tests/test_web_services.py`) are updated to the new
"create pending job only" behavior. No SQLite is introduced.

## Validation commands (requirement 13)

```bash
python -m pytest -v
python -m compileall app scripts
docker compose config
docker compose build
```

## Integration points

- **`feat/postgres-core`**: implement `app/repositories/postgres.py` honoring the
  ports and the SKIP LOCKED / dedup / stale contracts; add the `db` service,
  `depends_on`, `DATABASE_URL`, `SQLAlchemy`/`psycopg`, and DDL/migrations for the
  schema below.
- **`feat/auth-users-settings`**: repoint OAuth/settings writes to PostgreSQL;
  encrypt `refresh_token`, `client_secret`, and the per-user Deepgram key at rest;
  expose the per-user Deepgram key and `save_copy_to_drive` in the settings UI.

Schema deltas required from the other branches (relative to the existing design):

- `settings.save_copy_to_drive BOOLEAN NOT NULL DEFAULT false`
- `settings.deepgram_api_key_encrypted TEXT` (nullable; decrypted by the adapter)
- `transcription_jobs.started_at TIMESTAMPTZ` (nullable; supports stale detection)
- new `transcripts` table:
  `id`, `job_id` (FK), `user_id` (FK), `text` (TEXT), `json_payload` (JSONB),
  `drive_file_id` (TEXT, nullable), `created_at` (TIMESTAMPTZ).

## Out of scope

Whisper/local transcription, Celery, Redis, multi-worker coordination beyond
SKIP LOCKED, advanced UI, GitHub Actions, and the real PostgreSQL infrastructure
(owned by `feat/postgres-core`).

## Success criteria

- `python -m app.worker.main` runs a continuous claim→process loop.
- Worker never processes the same job twice (SKIP LOCKED contract; deterministic
  in tests via the in-memory lock).
- Jobs always end in a terminal state; none stuck in `processing` (stale recovery
  + guaranteed `failed` on exception).
- Transcripts (`text` + `json_payload`) are persisted via the repository.
- `POST /jobs/run-once` creates a pending job for the next not-completed video,
  without duplicates.
- `GET /jobs/{job_id}/download` returns the owner's (or admin's) completed
  transcript as a sanitized `text/plain` attachment.
- Per-user Deepgram key is used; no global key in the new flow.
- `WORKER_REPOSITORY_BACKEND` defaults to `postgres`; `memory` is documented as
  forbidden in production; `postgres` without `feat/postgres-core` fails clearly.
- Existing CLI worker modes still work; all validation commands pass.
