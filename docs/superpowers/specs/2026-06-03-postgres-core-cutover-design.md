# Design: Cutover SQLite → PostgreSQL (`feat/postgres-core`)

## Goal

Migrate the data layer from SQLite to PostgreSQL and remove SQLite from the
runtime entirely. Build a clean foundation with synchronous SQLAlchemy 2.0 +
Alembic that the sibling branches (`feat/auth-users-settings`,
`feat/ui-devops-polish`, `feat/postgres-worker`) build on.

This is a **full cutover**, decided with the user:

- SQLite is removed from the runtime; `app/db.py` is deleted.
- Everything that depended on `app/db.py` (the FastAPI web layer) is rewritten
  to use PostgreSQL repositories, keeping the existing routes functionally
  equivalent.
- Persistence tests run against **real PostgreSQL** (skipped when unavailable),
  never against a SQLite substitute. Pure logic is tested with fakes/mocks.

## Scope

In scope: database core (models, repositories, connection/session), Alembic,
docker-compose with a `postgres` service, the minimal web-layer rewrite needed
to drop `app/db.py`, requirements/config, and tests.

Out of scope (left for sibling branches): new UI, users screen, Deepgram
credentials screen, multiuser worker, GitHub Actions, Whisper/local
transcription.

## New core: `app/database/`

- `connection.py` — read/validate `DATABASE_URL` (must be
  `postgresql+psycopg://…`; clear error if missing/invalid); build the engine.
- `session.py` — `SessionLocal`, `get_db()` (FastAPI dependency),
  `session_scope()` (background tasks / worker), `init_engine()` for tests.
- `models.py` — `Base` + six SQLAlchemy 2.0 models + indexes/constraints.
- `repositories.py` — one repository class per table with
  `create` / `get` / `update` / `list` (+ `get_by_email` / `get_or_create`
  where useful).

`DATABASE_URL` is **required**. Importing the package does not connect; the
engine is created lazily so tests can point it at a test database first.

## Schema

Integer identity primary keys (preserves `user["id"]` semantics and the
session cookie payload).

- `users`: id, email (unique, not null), name, password_hash, role
  (admin/user), is_active (default true), created_at, updated_at.
- `google_tokens`: id, user_id → users.id, encrypted_access_token,
  encrypted_refresh_token, token_uri, client_id, client_secret, scopes (JSONB),
  expiry (timestamptz), created_at, updated_at. Unique on user_id.
- `deepgram_credentials`: id, user_id → users.id, encrypted_api_key,
  created_at, updated_at.
- `user_drive_settings`: id, user_id → users.id (unique), source/destination
  folder url/id/name, save_copy_to_drive (default false), created_at,
  updated_at. **No `poll_interval_seconds`** — see below.
- `transcription_jobs`: id, user_id → users.id, source_file_id,
  source_file_name, status, attempts (default 0), error_message,
  transcript_drive_file_id, created_at, updated_at, processed_at.
- `transcripts`: id, job_id → transcription_jobs.id (unique), user_id →
  users.id, transcript_text, transcript_json (JSONB), created_at.

Indexes: `transcription_jobs(user_id, status)`,
`transcription_jobs(user_id, source_file_id)`, `transcripts(user_id)`,
`google_tokens(user_id)`, `deepgram_credentials(user_id)`.

Dedupe of completed jobs: a PostgreSQL **partial unique index**
`UNIQUE (user_id, source_file_id) WHERE status = 'completed'` in the migration,
**plus** a guard in `TranscriptionJobRepository` (defense in depth, documented).

`deepgram_credentials` and `transcripts` get tables + repositories but are not
wired into the runtime yet (no Deepgram screen; run-once keeps uploading the TXT
to Drive and storing `transcript_drive_file_id`). They are ready for the sibling
branches.

## Worker poll interval

Per-user `poll_interval_seconds` is removed from `/settings`. The execution
interval is a **global worker env**, renamed `POLL_INTERVAL_SECONDS` →
`WORKER_POLL_INTERVAL_SECONDS` (the value was never read from the DB at runtime;
the worker reads it from the environment).

## Web layer rewrite (routes stay functionally equivalent)

`app/db.py` is deleted. `app/web/{config,token_store,services,main}.py` use
`get_db`/repositories:

- `WebSettings` drops `database_path`; the engine comes from `DATABASE_URL`.
- Routes (`/health`, `/login`, `/logout`, `/`, `/settings`, `/jobs`,
  `/jobs/run-once`, `/connect-google`, `/oauth/google/callback`) keep the same
  behavior. Login creates/loads the admin user (role `admin`). The background
  job uses `session_scope()`.
- `TokenStore` keeps its API (`save_for_user`/`get_for_user`), now writing the
  `encrypted_*` columns and `scopes` as a JSONB list.
- Templates switch from subscript (`job["status"]`) to attribute access
  (`job.status`); rendered output is identical. `settings.html` loses the
  poll-interval input.

## Migrations at runtime

The app does not create the schema on startup; the schema comes from Alembic
(`alembic upgrade head`, also runnable as
`docker compose run --rm web alembic upgrade head`). Tests create the schema via
`Base.metadata.create_all`.

## docker-compose

Services: `postgres` (volume `postgres_data:/var/lib/postgresql/data`,
healthcheck), `web`, `worker`. `web` and `worker` get
`DATABASE_URL=postgresql+psycopg://meet_user:…@postgres:5432/meet_transcription`.
The Dockerfile also copies `alembic/` and `alembic.ini`.

## Testing

- `tests/conftest.py` reads `TEST_DATABASE_URL` (default → the local Postgres),
  tries to connect, and **skips** persistence tests when Postgres is absent —
  no SQLite fallback. Schema via `create_all`; per-test isolation by truncation.
- Postgres integration: repositories CRUD + dedupe, token store persistence,
  web routes (TestClient), services (Drive/Deepgram fakes + real job rows).
- Pure logic (no DB): `DATABASE_URL` parse/validation, model metadata,
  `build_oauth_credentials`, Fernet encrypt/decrypt, transcript formatting,
  worker config parsing.

## Validation

`python -m pytest -v` (with Postgres up), `python -m compileall app scripts`,
`docker compose config`, `docker compose build`, plus `alembic upgrade head`
against real Postgres.

## Risks / pending for integration

- `transcripts` and `deepgram_credentials` exist but are not populated/used at
  runtime yet.
- Per-user Deepgram key and transcript-text persistence are sibling-branch work.
- `password_hash` / `role` / `is_active` are ready, but there is no signup or
  user-management flow yet.
- `DATABASE_URL` changes meaning from a SQLite file path to a PostgreSQL URL;
  deployments must update it.

## Update — compatibility bridges (auth + worker)

`app/database/` stays the canonical PostgreSQL core. On top of it, two edge
adapters expose it in the exact shapes the sibling branches were built against,
so postgres-core integrates without forcing the consumers to touch ORM objects:

- **auth** (`feat/auth-users-settings`): `app/db/postgres.py` provides
  `build_repositories(database_url) -> RepositoryBundle` whose repositories
  satisfy the Protocols in that branch's `app/web/repositories.py` and return its
  frozen dataclasses (`User`, `GoogleToken`, `DriveSettings`, `Job` — timestamps
  as ISO strings, `scopes` as a string).
- **worker** (`feat/postgres-worker`): `app/repositories/postgres.py` provides
  `build_postgres_repositories()` returning the `Repositories` bundle whose
  members satisfy `app/core/ports.py` and return its dataclasses (timestamps as
  `datetime`). `JobRepository.claim_next_pending_job` uses
  `SELECT … FOR UPDATE SKIP LOCKED`.

Edge translation, good database underneath: `scopes` stays JSONB in PostgreSQL
and is converted to/from a string at the boundary; sensitive token/key fields
cross the boundary as ciphertext (the web/worker layer decrypts).

Each adapter imports the real consumer contract when present and falls back to a
vendored copy (`app/db/_auth_contract.py`, `app/repositories/_worker_contract.py`)
so the bridges import and test standalone on this branch. `app/repositories/` has
no `__init__.py` (PEP 420 namespace package) so it merges cleanly with the
worker's `app/repositories/__init__.py`.

Schema additions for integration: `users.google_email`, `users.google_name`,
`transcription_jobs.started_at`, `transcripts.drive_file_id` — folded into the
single initial migration `0001_initial` (branch not yet pushed).
