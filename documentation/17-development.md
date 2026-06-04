# Development

This document is the working guide for changing the Meet Transcription codebase:
where the code lives, the patterns you must follow, how to run the tests, how to
branch and contribute, and the validation commands that must pass before you
finish. It is the operational companion to [Architecture](01-architecture.md);
read that first for the runtime picture, then come here to write code.

The hard rules in [`CLAUDE.md`](../CLAUDE.md) are authoritative. This document
expands them with concrete file paths and commands — it never overrides them.

---

## Prerequisites

| Tool | Why |
| --- | --- |
| Python 3.11 | Same minor as the base image (`python:3.11-slim`). |
| `pip` + a virtualenv | Install `requirements.txt` for local runs and tests. |
| Docker + Compose v2 | `docker compose config/build` validation; full stack. |
| PostgreSQL 16 (optional) | Only for the persistence tests; otherwise they `skip`. |

The runtime dependencies are pinned in [`requirements.txt`](../requirements.txt).
Note what is **not** there: the local transcription engines. faster-whisper is
installed only at image build time (build arg `INSTALL_FASTER_WHISPER=true`), and
whisper.cpp is provided as an external `WHISPER_CPP_BINARY`. Do not add them to
`requirements.txt`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Code layout

All application code lives under `app/`. The package is intentionally split so
that the **web** (request/response) and **worker** (long-running jobs) concerns
stay separate even though they ship in one image. The legacy CLI sits at the top
level (`app/main.py`) and must keep working.

```
app/
├── web/            FastAPI app, OAuth, server-rendered UI (request path only)
├── worker/         Out-of-band job processor (the ONLY place that transcribes)
├── queue/          Redis/in-memory queue + global lock + requeue
├── transcription/  Pluggable provider layer (Deepgram + local CPU engines)
├── database/       SQLAlchemy models, engine/session, ORM repositories
├── repositories/   JobRepository adapters (postgres + memory) behind the port
├── services/       Reusable application services (download, jobs)
├── core/           Domain models + repository ports (the contracts)
├── db/             Auth-view repository bridge over Postgres
├── errors.py       AppError hierarchy with secret-free user_message
├── main.py         Legacy env-driven CLI (python -m app.main)
└── ...             config.py, logger.py, drive_client.py, deepgram_client.py, ...
```

### `app/web/` — the request path

`uvicorn app.web.main:app`. Server-rendered FastAPI: routes, session auth, OAuth,
and the dashboard/settings/jobs UI. Key files:

| File | Responsibility |
| --- | --- |
| `app/web/main.py` | FastAPI app + all routes (`/health`, `/login`, `/`, `/settings`, `/jobs`, `/jobs/run-once`, `/jobs/{id}`, `/jobs/{id}/download`, `/admin/users`, `/connect-google`, `/oauth/google/callback`). |
| `app/web/repositories.py` | Auth-view repository bundle (users, tokens, Deepgram keys, Drive settings, jobs). |
| `app/web/security.py` | Fernet encryption derived from `APP_SECRET_KEY`. |
| `app/web/token_store.py`, `deepgram_key.py` | Encrypt-at-rest helpers for Google tokens and per-user Deepgram keys. |
| `app/web/passwords.py` | bcrypt password hashing/verification. |
| `app/web/drive_links.py`, `helpers.py` | Drive URL → id parsing; `mid`/`dt`/`drive_dl` Jinja filters (`mono` is a CSS class, not a filter). |
| `app/web/templates/`, `app/web/static/styles.css` | Jinja2 templates + local CSS. **No React, no CDN, no build step.** |

The web service **never** downloads, transcribes, or uploads. `/jobs/run-once`
validates input, creates a `pending` job in Postgres, and enqueues its `job_id` to
Redis — nothing more. See [Web UI](12-web-ui.md).

### `app/worker/` — the job processor

`python -m app.worker.main`. The only place where download / transcribe / upload
happens.

| File | Responsibility |
| --- | --- |
| `app/worker/main.py` | Entry point: `run()` recovers stale jobs, then runs the queue loop (`WORKER_CONCURRENCY` threads) when a queue is configured, else the legacy poll loop. |
| `app/worker/container.py` | Dependency wiring (`WorkerContainer`, `build_container`). |
| `app/worker/queue_loop.py` | Redis path (`run_queue_loop`): dequeue → global lock → `claim_job`. |
| `app/worker/loop.py` | Legacy poll path (`run_worker_loop`): `claim_next_pending_job`. |
| `app/worker/processor.py` | The per-job pipeline (`JobProcessor.process`). |
| `app/worker/config.py` | `WorkerSettings` from `WORKER_*` env vars. |

See [Worker Flow](11-worker-flow.md).

### `app/queue/` — queue and lock

`TranscriptionQueue` port plus adapters. Backend chosen by `QUEUE_BACKEND`
(`redis` | `memory` | `none`; **default `none` in code**, `redis` in
`docker-compose.yml`).

| File | Responsibility |
| --- | --- |
| `app/queue/ports.py` | The `TranscriptionQueue` protocol. |
| `app/queue/redis_queue.py` | Redis adapter: keys `transcription:queue` (list), `transcription:queued` (dedupe set), `transcription:global_lock` (token). |
| `app/queue/memory_queue.py` | In-process adapter for dev/tests. |
| `app/queue/__init__.py`, `config.py` | `build_queue` + `requeue_pending_jobs`; `QueueSettings`. |

See [Redis Queue](09-redis-queue.md).

### `app/transcription/` — provider layer

The pluggable transcription engines and the rule that selects one.

| File | Responsibility |
| --- | --- |
| `app/transcription/provider.py` | `get_transcription_provider_status` — the selection rule. |
| `app/transcription/factory.py` | `build_local_provider`, `resolve_provider`, `LocalTranscriptionUnavailable` back-compat alias. |
| `app/transcription/deepgram_provider.py` | Wraps `DeepgramClient`; keeps the legacy `.txt` format; raw kept under `payload.raw`. |
| `app/transcription/faster_whisper_provider.py` | `WhisperModel` (device=cpu); imports `faster_whisper` lazily. |
| `app/transcription/whisper_cpp_provider.py` | ffmpeg → `whisper-cli -oj` JSON → parse offsets. |
| `app/transcription/audio.py` | ffmpeg command builder. |
| `app/transcription/normalizer.py` | Normalized transcript schema + `render_local_text`. |
| `app/transcription/local_validation.py` | Injectable validation probes. |
| `app/transcription/config.py` | `LOCAL_TRANSCRIPTION_*` config. |

The selection rule, verbatim from the code:

- `LOCAL_TRANSCRIPTION_ENABLED=false` → Deepgram; per-user key required.
- enabled **and** valid → local engine; **no** Deepgram key required.
- enabled **and** invalid → Deepgram required; `run-once` blocked unless a
  Deepgram key is set. **No silent fallback.**

See [Local Transcription](06-local-transcription.md),
[faster-whisper](07-faster-whisper.md), and [whisper.cpp](08-whisper-cpp.md).

### `app/database/` — SQLAlchemy + ORM repositories

| File | Responsibility |
| --- | --- |
| `app/database/models.py` | All tables: `users`, `google_tokens`, `deepgram_credentials`, `user_drive_settings`, `transcription_jobs`, `transcripts`. |
| `app/database/connection.py` | `normalize_database_url` (rejects/normalizes the URL; never SQLite). |
| `app/database/session.py` | Global engine + `session_scope` / `get_sessionmaker`. |
| `app/database/repositories.py` | ORM-backed repository implementations. |

Schema changes are delivered by Alembic migrations under `alembic/versions/`
(initial: `0001_create_initial_postgres_schema.py`). See
[Postgres and Migrations](10-postgres-and-migrations.md).

### `app/repositories/` — the `JobRepository` adapters

The contract lives in `app/core/ports.py`; the two implementations live here.

| File | Responsibility |
| --- | --- |
| `app/repositories/postgres.py` | `build_postgres_repositories()` — the production adapter. |
| `app/repositories/memory.py` | `build_memory_repositories()` — dict-backed fake for dev/tests. |
| `app/repositories/__init__.py` | `build_repositories(backend)` selector (`postgres` default, `memory` for dev/tests). |

### `app/services/` — application services

| File | Responsibility |
| --- | --- |
| `app/services/download_service.py` | Streams a completed transcript for the `/jobs/{id}/download` route. |
| `app/services/job_service.py` | Job creation/validation helpers shared by routes. |

### `app/core/` — domain models and ports

| File | Responsibility |
| --- | --- |
| `app/core/models.py` | Plain domain dataclasses (`Job`, `GoogleToken`, `Settings`, `Transcript`). |
| `app/core/ports.py` | The `JobRepository` / `Repositories` protocols — the contract both adapters honor. |

---

## The repository-interface pattern

Database access goes through **ports** (typed Protocols), never directly through
raw SQL or ORM objects in callers. There are two views over the same Postgres
tables:

- the **worker view** — `JobRepository` in `app/core/ports.py`; and
- the **auth view** — `RepositoryBundle` in `app/web/repositories.py`.

The `JobRepository` contract is exactly these methods — use these names, never
invent conflicting ones:

```
claim_next_pending_job   claim_job              list_pending_jobs
create_job               get_job                mark_completed
mark_failed              find_existing_job      reset_stale_processing_jobs
list_jobs_for_user
```

`JobRepository` is declared `@runtime_checkable` in `app/core/ports.py`. When you
add a method to the contract you must:

1. add it to the Protocol in `app/core/ports.py`;
2. implement it in **both** adapters — `app/repositories/postgres.py` and
   `app/repositories/memory.py`; and
3. update the conformance stub `tests/test_core_ports.py::_Stub`.

### Dict-backed in-memory fakes

Tests **never** use SQLite — not even in-memory. The fakes are plain Python dicts
that satisfy the same ports as the Postgres adapters:

- `app/repositories/memory.py` → `build_memory_repositories()` backs the worker
  view (`WORKER_REPOSITORY_BACKEND=memory`).
- `tests/fakes.py` (e.g. `InMemoryUsersRepository`) backs the auth view and
  builds a `RepositoryBundle` from `app/web/repositories.py`.
- `tests/support.py` wires a `WorkerContainer` for processor tests with
  `FakeDriveClient` / `FakeDeepgramClient` and `make_worker_settings(...,
  repository_backend="memory")`.

Local transcription engines are likewise faked: providers accept injectable
`model_factory` / `runner` / `audio_extractor` so **no real model is ever
downloaded in tests**.

The backend selector enforces the boundary:

```python
# app/repositories/__init__.py
def build_repositories(backend: str | None = None) -> Repositories:
    # default 'postgres' (production); 'memory' is dev/tests only.
```

`WORKER_REPOSITORY_BACKEND=memory` is dev/tests only and is forbidden in
production.

---

## Running the tests

The test suite is `pytest`. From the repo root, with the virtualenv active:

```bash
python -m pytest -v          # full suite, verbose
python -m pytest -q          # quiet (same set as CI)
python -m pytest tests/test_worker_processor.py        # one module
python -m pytest tests/test_web_routes.py -k run_once   # one keyword
```

Around 256 tests pass. The pure-logic tests (queue, providers, normalizer, web
routes via `TestClient`, repository fakes) need no external services.

### PostgreSQL persistence tests

The persistence tests (`test_repositories.py`, `test_web_repositories.py`,
`test_database_config.py`, and others that use the `engine` / `pg` / `db`
fixtures in `tests/conftest.py`) run against a **real** PostgreSQL. When no
database is reachable they **`skip`** — they never silently fall back to SQLite.

To run them, point `TEST_DATABASE_URL` (falls back to `DATABASE_URL`) at a
disposable database:

```bash
docker run -d --name meet_pg_test \
  -e POSTGRES_DB=meet_test -e POSTGRES_USER=meet_user \
  -e POSTGRES_PASSWORD=meet_password -p 55432:5432 postgres:16

export TEST_DATABASE_URL=postgresql+psycopg://meet_user:meet_password@localhost:55432/meet_test
python -m pytest -v
```

The `engine` fixture drops and re-creates the schema for the session and rebinds
the application's global engine; the `pg` / `db` fixtures `TRUNCATE ... RESTART
IDENTITY CASCADE` after each test.

---

## Validation commands

Run **all** of these before you finish — this is the same gate CI applies (see
[GHCR / CI](14-ghcr.md)):

```bash
python -m pytest -v                 # tests must pass
python -m compileall app scripts    # syntax-check the whole package
docker compose config               # validate compose (needs a local .env)
docker compose build                # the shared web/worker image must build
```

For `docker compose config` / `build` you need a local `.env`:

```bash
cp .env.example .env                # then fill in the required values
```

See [Environment Variables](03-environment-variables.md) for every variable.

---

## Branching and contributing

The integration branch is `integration/postgres-platform`; the default branch is
`main`. The codebase was built across feature branches forked from a common
commit and integrated here:

| Branch | Concern |
| --- | --- |
| `feat/ui-devops-polish` | UI, Docker, CI, docs. |
| `feat/auth-users-settings` | Auth, users/roles, per-user Google OAuth, per-user encrypted Deepgram key (no env fallback), Drive settings by URL. |
| `feat/postgres-core` | SQLAlchemy + PostgreSQL repositories and tables. |
| `feat/postgres-worker` | The `app.worker.main` job processor. |

Workflow:

1. Branch from the integration branch:
   `git switch -c feat/<short-topic> integration/postgres-platform`.
2. Make the change inside the right package boundary (web vs worker vs
   transcription vs repositories) — keep the contracts intact.
3. Add or update tests with the change (dict-backed fakes; no SQLite).
4. Run the four validation commands above.
5. Open a PR targeting `main` (CI runs on PRs to `main` and on pushes to `main`
   and `integration/postgres-platform`).

Never commit secrets: `.env`, `secrets/*.json`, `token.json`, and
`data/processed_files.json` are git-ignored and must stay that way. See
[Security](16-security.md).

---

## Coding conventions

These are non-negotiable. Violating any of them breaks the architecture.

### PostgreSQL only — no SQLite, anywhere

PostgreSQL is the single source of truth. The legacy `app/db.py` / `sqlite3` has
been removed. `DATABASE_URL` is always
`postgresql+psycopg://user:pass@postgres:5432/db`. New code depends on the
repository **interfaces** (`app/core/ports.py`, `app/web/repositories.py`), not on
raw SQL in callers. Tests use dict-backed fakes — never SQLite, not even
in-memory.

### Redis is the queue/lock, not the source of truth

`claim_job` is the atomic final dedupe defense in Postgres; anything Redis loses
must be recoverable from Postgres (`requeue_pending_jobs`). The worker default
`QUEUE_BACKEND=none` keeps the legacy poll loop; `redis` is the production mode.

### No heavy work in the web request

Never download, transcribe, or upload inside an HTTP route. The request path only
validates, creates a `pending` job, and enqueues its id. All heavy work happens in
the worker.

### Server-rendered templates only

The UI is Jinja2 in `app/web/templates/` with local CSS in
`app/web/static/styles.css`. No React/SPA, no CDN assets, no build step. Truncate
long Drive ids (`mid`/`mono`) and render timestamps via the `dt` filter
(`app/web/helpers.py`).

### Encryption at rest

Google tokens and per-user Deepgram keys are always Fernet-encrypted (key derived
from `APP_SECRET_KEY` in `app/web/security.py`). Never store them in plaintext,
and never log secrets — a test enforces this. The web/worker deployment does
**not** use a global `DEEPGRAM_API_KEY`; keys are per-user.

### Local transcription is CPU-only and off by default

No GPU. The heavy engines are gated behind Docker build args
(`INSTALL_FASTER_WHISPER`, `INSTALL_WHISPER_CPP`), imported lazily, and never
installed at container startup. Errors map to friendly `user_message`s via the
`AppError` hierarchy in `app/errors.py` (e.g. `LocalTranscriptionUnavailableError`,
`DeepgramKeyRequiredError`, `WhisperCppBinaryNotFoundError`,
`QueueUnavailableError`); tracebacks stay in logs, never in the UI.

### Keep the legacy CLI working

`python -m app.main` (`--once` / `--watch` / `--reprocess`) is env-driven, uses a
mounted `token.json` or service account, stores state in
`data/processed_files.json`, and reads the global `DEEPGRAM_API_KEY`. It is a
supported compatibility deployment (not a compose service). Its flags and behavior
must keep working; do not regress it when changing the web/worker stack.

---

## Where to make a change

| You want to... | Touch... | Don't forget |
| --- | --- | --- |
| Add a route or UI panel | `app/web/main.py`, `app/web/templates/`, `styles.css` | No heavy work in-request; server-rendered only. |
| Change job processing | `app/worker/processor.py` | Friendly `user_message` on failure; scratch cleanup. |
| Add a `JobRepository` method | `app/core/ports.py` + both adapters | Update `tests/test_core_ports.py::_Stub`. |
| Change the schema | `app/database/models.py` + a new `alembic/versions/*.py` | Migration runs as the `migrate` service. |
| Add/adjust a provider | `app/transcription/` | Lazy import; injectable for tests; respect the selection rule. |
| Change queue/lock behavior | `app/queue/` | Postgres stays authoritative; `requeue_pending_jobs` recovers. |

Always finish by running the four validation commands.
