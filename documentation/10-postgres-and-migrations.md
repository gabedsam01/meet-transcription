# PostgreSQL and Migrations

PostgreSQL is the **single source of truth** for Meet Transcription. Users,
encrypted Google tokens, encrypted Deepgram keys, Drive settings, transcription
jobs, and the transcripts themselves all live in Postgres. There is **no SQLite
mode** anywhere in the web/worker deployment — not in production, not in
development, and not in the test suite (see [No-SQLite rule](#no-sqlite-rule)).

This document covers the schema (every table and its key columns), the one-shot
`migrate` service that runs `alembic upgrade head`, how `web` and `worker` wait
for it, how to run migrations by hand, and how to back up and restore the
`postgres_data` volume with `pg_dump` / `pg_restore`.

See also: [Architecture](01-architecture.md) for the overall service topology.

## Postgres as the source of truth

The `postgres` service in `docker-compose.yml` runs `postgres:16` and is the
authoritative store for all application state:

```yaml
postgres:
  image: postgres:16
  restart: unless-stopped
  environment:
    POSTGRES_DB: ${POSTGRES_DB:-meet_transcription}
    POSTGRES_USER: ${POSTGRES_USER:-meet_user}
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-change_me}
  volumes:
    - postgres_data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-meet_user} -d ${POSTGRES_DB:-meet_transcription}"]
    interval: 5s
    timeout: 5s
    retries: 10
    start_period: 10s
```

Key points:

- The database files persist in the named volume `postgres_data`
  (mounted at `/var/lib/postgresql/data`). **Backing up the deployment means
  backing up this volume** (or `pg_dump`-ing the database — see below).
- The healthcheck runs `pg_isready` against the configured user/db. The other
  services depend on `condition: service_healthy`, so nothing starts before
  Postgres is accepting connections.
- All application code reaches the database through the SQLAlchemy repository
  layer over the `DATABASE_URL`. The URL is always a `postgresql+psycopg://`
  DSN — for example:

  ```
  DATABASE_URL=postgresql+psycopg://meet_user:change_me@postgres:5432/meet_transcription
  ```

  Never a `sqlite://` URL.

### Connection / Postgres environment variables

| Variable | Used by | Default in `docker-compose.yml` | Purpose |
|---|---|---|---|
| `POSTGRES_DB` | `postgres` | `meet_transcription` | Database name created on first boot. |
| `POSTGRES_USER` | `postgres` | `meet_user` | Superuser/role created on first boot. |
| `POSTGRES_PASSWORD` | `postgres` | `change_me` | Role password (**override in production**). |
| `DATABASE_URL` | `migrate`, `web`, `worker` | `postgresql+psycopg://meet_user:change_me@postgres:5432/meet_transcription` | SQLAlchemy DSN. **Must be `postgresql+psycopg://`, never `sqlite`.** |

`POSTGRES_*` configure the server container; `DATABASE_URL` configures the
clients (`migrate`, `web`, `worker`). When you change the user, password, or db
name, update **both** sides so the DSN matches the server.

## Schema: tables and key columns

The schema is defined in `app/database/models.py` (SQLAlchemy ORM) and created
by the Alembic migration
`alembic/versions/0001_create_initial_postgres_schema.py` (revision
`0001_initial`). The migration is hand-written to mirror the models exactly:
index and constraint names match what `Base.metadata.create_all` would produce.

The schema is **PostgreSQL-only**: it uses `JSONB` columns and a partial unique
index directly. All timestamps are timezone-aware and server-default to
`now()`. Most tables carry `created_at` / `updated_at` (via `TimestampMixin`);
`transcripts` is immutable and has only `created_at`.

### `users`

Application accounts (local + Google-linked identity).

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` PK | |
| `email` | `String(320)` | `NOT NULL`, unique (`ix_users_email`, unique). |
| `name` | `String(255)` | nullable. |
| `password_hash` | `Text` | nullable (Google-only users may have none). |
| `role` | `String(20)` | `NOT NULL`, default `'user'`; constrained `IN ('admin','user')` via `ck_users_role`. |
| `is_active` | `Boolean` | `NOT NULL`, default `true`. |
| `google_email` | `Text` | nullable; captured at OAuth connect time. |
| `google_name` | `Text` | nullable. |
| `created_at` / `updated_at` | `DateTime(tz)` | server-default `now()`. |

### `google_tokens`

Per-user Google OAuth credentials, **encrypted at rest** (Fernet, key derived
from `APP_SECRET_KEY` via `app/web/security.py`).

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` PK | |
| `user_id` | `Integer` FK → `users.id` | `ON DELETE CASCADE`, unique (one token row per user). |
| `encrypted_access_token` | `Text` | `NOT NULL`, encrypted. |
| `encrypted_refresh_token` | `Text` | nullable, encrypted. |
| `token_uri` | `Text` | `NOT NULL`. |
| `client_id` | `Text` | nullable. |
| `client_secret` | `Text` | nullable. |
| `scopes` | `JSONB` | nullable; requested scope is `https://www.googleapis.com/auth/drive`. |
| `expiry` | `DateTime(tz)` | nullable. |

### `deepgram_credentials`

Per-user Deepgram API key, **encrypted at rest**. There is no global
`DEEPGRAM_API_KEY` in the web/worker deployment — keys are per user.

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` PK | |
| `user_id` | `Integer` FK → `users.id` | `ON DELETE CASCADE`, unique (one key per user). |
| `encrypted_api_key` | `Text` | `NOT NULL`, encrypted. |

### `user_drive_settings`

Per-user Drive folder configuration (input source + optional TXT backup
destination).

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` PK | |
| `user_id` | `Integer` FK → `users.id` | `ON DELETE CASCADE`, unique. |
| `source_drive_folder_url` / `source_drive_folder_id` / `source_drive_folder_name` | `Text` | nullable; the watched input folder. |
| `destination_drive_folder_url` / `destination_drive_folder_id` / `destination_drive_folder_name` | `Text` | nullable; the optional TXT-backup folder. |
| `save_copy_to_drive` | `Boolean` | `NOT NULL`, default `false`. The worker uploads a TXT copy only when this is true **and** a destination is set. |

### `transcription_jobs`

The work queue of record. Statuses are `pending`, `processing`, `completed`,
`failed` (stored as `Text`, default `'pending'`).

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` PK | |
| `user_id` | `Integer` FK → `users.id` | `ON DELETE CASCADE`. |
| `source_file_id` | `Text` | nullable; the Drive MP4 file id. |
| `source_file_name` | `Text` | nullable. |
| `status` | `Text` | `NOT NULL`, default `'pending'`. |
| `attempts` | `Integer` | `NOT NULL`, default `0`. |
| `error_message` | `Text` | nullable; the friendly, secret-free `user_message` on failure (never a traceback). |
| `transcript_drive_file_id` | `Text` | nullable; Drive file id of the uploaded TXT, if any. |
| `started_at` | `DateTime(tz)` | nullable; stamped when the worker claims the job. |
| `processed_at` | `DateTime(tz)` | nullable; stamped at terminal state. |
| `created_at` / `updated_at` | `DateTime(tz)` | server-default `now()`. |

Indexes:

- `ix_transcription_jobs_user_status` on `(user_id, status)`.
- `ix_transcription_jobs_user_source` on `(user_id, source_file_id)`.
- `uq_transcription_jobs_completed_source` — a **partial unique index** on
  `(user_id, source_file_id)` `WHERE status = 'completed'`. This enforces the
  dedupe rule: a user cannot have two completed jobs for the same source file.

### `transcripts`

The transcription result. Immutable: only `created_at`, no `updated_at`.

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` PK | |
| `job_id` | `Integer` FK → `transcription_jobs.id` | `ON DELETE CASCADE`, unique (one transcript per job). |
| `user_id` | `Integer` FK → `users.id` | `ON DELETE CASCADE`, indexed. |
| `transcript_text` | `Text` | `NOT NULL`; the human-readable `.txt` served by **Download TXT**. |
| `transcript_json` | `JSONB` | nullable; the normalized transcript schema (see below). |
| `drive_file_id` | `Text` | nullable; Drive file id of the uploaded transcript. |
| `created_at` | `DateTime(tz)` | server-default `now()`. |

The normalized object stored in `transcript_json` is:

```json
{
  "provider": "local | deepgram",
  "engine": "faster-whisper | whisper-cpp | deepgram",
  "model": "...",
  "language": "...",
  "text": "...",
  "segments": [{"start": 0.0, "end": 0.0, "speaker": null, "text": "..."}],
  "words": [],
  "utterances": [],
  "raw": {}
}
```

The local MVP has no diarization, so `speaker` is `null` for local engines.

### Foreign keys and cascades

Every child table references `users.id` with `ON DELETE CASCADE`, and
`transcripts.job_id` references `transcription_jobs.id` with `ON DELETE
CASCADE`. Deleting a user removes their tokens, Deepgram key, Drive settings,
jobs, and transcripts atomically.

## The `migrate` one-shot service

Schema migrations are applied by a dedicated one-shot service that runs
`alembic upgrade head` once and exits:

```yaml
migrate:
  <<: *app
  command: ["alembic", "upgrade", "head"]
  restart: "no"   # one-shot job: must NOT be restarted after it exits 0.
  environment:
    DATABASE_URL: ${DATABASE_URL:-postgresql+psycopg://meet_user:change_me@postgres:5432/meet_transcription}
```

Details:

- It reuses the **same application image** as `web` and `worker` (the
  `x-app` anchor, `ghcr.io/gabedsam01/meet-transcription:latest`, built from
  `./Dockerfile`). `alembic` and `alembic.ini` are baked into that image.
- It inherits `depends_on: postgres → service_healthy` from the `x-app`
  anchor, so it waits for Postgres before running.
- `restart: "no"` is deliberate: after the migration succeeds and the
  container exits `0`, Compose must **not** restart it. It is a job, not a
  long-running service.
- It uses the same `DATABASE_URL` as the app, so it migrates the exact
  database the app reads.

### How `web` and `worker` wait for it

Both `web` and `worker` override the anchor's `depends_on` to wait for the
schema, not merely for Postgres to be reachable:

```yaml
depends_on:
  postgres:
    condition: service_healthy
  redis:
    condition: service_healthy
  migrate:
    condition: service_completed_successfully
```

`service_completed_successfully` means Compose will not start `web` or `worker`
until the `migrate` container has exited with status `0`. This guarantees the
schema is current **before any HTTP request is served or any job is claimed**.

### Full startup order

```
postgres  ── healthcheck passes (pg_isready) ──┐
redis     ── healthcheck passes (redis-cli ping) ─┤
                                                  ▼
migrate   ── runs `alembic upgrade head`, exits 0 ─┐
                                                    ▼
web + worker ── start (depends_on: migrate completed)
```

There are 5 services total: `postgres`, `redis`, `migrate`, `web`, `worker`.
`web` and `worker` share one image with different `command`s.

## Running migrations manually

You normally do not need to run migrations by hand — the `migrate` service does
it on every `docker compose up`. Use the commands below for one-off operations,
debugging, or local development.

### Apply all pending migrations (upgrade head)

Run the same command the `migrate` service runs, on demand:

```bash
docker compose run --rm migrate
```

`migrate`'s `command` is already `alembic upgrade head`, so `run --rm migrate`
applies all pending migrations and removes the container afterward. To bring up
just the prerequisites first:

```bash
docker compose up -d postgres
docker compose run --rm migrate
```

You can also invoke Alembic explicitly inside any app container (they all carry
`alembic` and the correct `DATABASE_URL`):

```bash
# Apply everything up to the latest revision
docker compose run --rm web alembic upgrade head

# In a running container instead of a fresh one
docker compose exec web alembic upgrade head
```

### Inspect and move between revisions

```bash
# Show the revision currently applied to the database
docker compose run --rm web alembic current

# Show the full migration history (current head is 0001_initial)
docker compose run --rm web alembic history --verbose

# Roll back the last migration (runs downgrade())
docker compose run --rm web alembic downgrade -1
```

The baseline revision is `0001_initial`
(`alembic/versions/0001_create_initial_postgres_schema.py`), whose
`down_revision` is `None`. Its `downgrade()` drops every table in reverse
dependency order.

### Running Alembic outside Docker (local dev)

If you have a local Python environment and a reachable Postgres, point
`DATABASE_URL` at it and run Alembic directly from the repo root:

```bash
export DATABASE_URL=postgresql+psycopg://meet_user:change_me@localhost:5432/meet_transcription
alembic upgrade head
```

Always use a `postgresql+psycopg://` DSN — Alembic targets PostgreSQL features
(`JSONB`, the partial unique index) that have no SQLite equivalent.

## The `postgres_data` volume and backups

All Postgres state lives in the named volume `postgres_data`, declared at the
bottom of `docker-compose.yml`:

```yaml
volumes:
  postgres_data:
  redis_data:
```

`postgres_data` is mounted at `/var/lib/postgresql/data` in the `postgres`
service. **Backing up the deployment = backing up this volume**, either as a
logical SQL dump (`pg_dump`, recommended and portable) or as a raw volume
archive.

> Redis (`redis_data`) holds only the transient queue and the global lock; it is
> not a backup target. Postgres is the source of truth.

### Logical backup with `pg_dump` (recommended)

Run `pg_dump` inside the `postgres` container and stream the output to a file on
the host. Use the custom format (`-Fc`) for a compressed, selectively
restorable archive:

```bash
# Custom-format dump (compressed; restore with pg_restore)
docker compose exec -T postgres \
  pg_dump -U meet_user -d meet_transcription -Fc \
  > backup-$(date +%F).dump
```

Or a plain SQL dump (human-readable, restore by piping into `psql`):

```bash
docker compose exec -T postgres \
  pg_dump -U meet_user -d meet_transcription \
  > backup-$(date +%F).sql
```

Adjust `-U` / `-d` if you overrode `POSTGRES_USER` / `POSTGRES_DB`.

### Restore from a `pg_dump` backup

For a custom-format (`-Fc`) dump, use `pg_restore`. The example below cleans
existing objects first (`--clean --if-exists`):

```bash
docker compose exec -T postgres \
  pg_restore -U meet_user -d meet_transcription --clean --if-exists \
  < backup-2026-06-04.dump
```

For a plain SQL dump, pipe it into `psql`:

```bash
docker compose exec -T postgres \
  psql -U meet_user -d meet_transcription \
  < backup-2026-06-04.sql
```

Restore into a database whose schema is at the matching Alembic revision (or an
empty database). After restoring an older dump, run `alembic upgrade head`
(via the `migrate` service or `docker compose run --rm migrate`) to bring the
schema forward.

### Raw volume backup (alternative)

You can also archive the volume directory directly. Stop Postgres first so the
files are consistent:

```bash
docker compose stop postgres

# Tar the volume contents to a host file
docker run --rm \
  -v meet-transcricao_postgres_data:/data \
  -v "$PWD":/backup \
  alpine tar czf /backup/postgres_data-$(date +%F).tar.gz -C /data .

docker compose start postgres
```

> The volume name is prefixed by the Compose project (typically the directory
> name), e.g. `meet-transcricao_postgres_data`. Confirm with
> `docker volume ls`.

Restore by extracting the archive back into a fresh `postgres_data` volume while
the `postgres` service is stopped. For portability across Postgres versions,
prefer the `pg_dump` / `pg_restore` route over raw volume archives.

## No-SQLite rule

PostgreSQL is the **only** database in the web/worker architecture. This is a
hard rule, enforced throughout the codebase:

- `DATABASE_URL` is always a `postgresql+psycopg://` DSN. Never `sqlite://`.
- The schema depends on PostgreSQL-specific features — `JSONB` columns and the
  partial unique index `uq_transcription_jobs_completed_source`
  (`WHERE status = 'completed'`) — which have no SQLite equivalent.
- Database access goes through the repository contract
  (`app/core/ports.py` `JobRepository`). The production adapter is
  `app/repositories/postgres.py`. The only in-memory option is a **dict-backed
  fake** (`app/repositories/memory.py`, selected with
  `WORKER_REPOSITORY_BACKEND=memory`), used for development and tests — it is
  **not** SQLite.
- Tests use the dict-backed fakes. PostgreSQL integration tests **skip** when
  `TEST_DATABASE_URL` / `DATABASE_URL` is unreachable; they never fall back to
  SQLite.

If you are adding persistence, depend on the repository interface and the
Postgres adapter. Do not introduce `sqlite3`, a `sqlite://` URL, or any other
database engine.
