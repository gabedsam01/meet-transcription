# Testing

This document explains how the meet-transcription test suite is organized, how
to run it, and the design rules that keep it fast, deterministic, and
production-faithful. Two rules from the project's hard constraints drive
everything here:

- **PostgreSQL is the single source of truth — there is no SQLite anywhere**,
  including in tests. Database-backed tests run against a real PostgreSQL
  instance and `skip` when none is reachable; they never silently fall back to
  SQLite.
- **Real local transcription models are never downloaded during tests.** The
  local engines (`faster-whisper`, `whisper.cpp`) are exercised through
  injectable seams (`model_factory` / `runner` / `audio_extractor`).

See also: [Architecture](01-architecture.md), [Worker](11-worker-flow.md),
[Local Transcription](06-local-transcription.md), and [CI/CD](14-ghcr.md) for the
surrounding context referenced below.

## How the suite is organized

The tests live under `tests/` and split into two broad categories:

| Category | What it covers | External dependencies |
| --- | --- | --- |
| **Unit / behavior tests** | Worker flow, transcription providers, factory/provider resolution, queue, web routes, helpers, errors | None — pure Python with in-memory fakes |
| **PostgreSQL integration tests** | The SQLAlchemy repository adapters, schema, migrations, and any code path that hits the real DB (TestClient, background jobs) | A reachable PostgreSQL 16; otherwise the tests `skip` |

There are currently **256 passing tests**. The unit tier runs everywhere with no
setup; the PostgreSQL tier only runs when a database URL is reachable.

Three shared helper modules support the suite:

- `tests/conftest.py` — pytest fixtures for the real PostgreSQL database
  (`engine`, `pg`, `db`).
- `tests/fakes.py` — dict-backed, in-memory implementations of the **web**
  repository bundle (`RepositoryBundle`) used by auth/UI tests.
- `tests/support.py` — builders for the **worker** side: fake Drive/Deepgram
  clients and a fully wired `WorkerContainer` with every collaborator injected.

## Unit tests with in-memory dict fakes

Unit tests never touch a database, the network, Drive, Deepgram, or a real
model. They use dict-backed fakes that satisfy the same repository and client
contracts as production code.

### Web / auth repositories (`tests/fakes.py`)

`build_fake_repositories()` returns a `RepositoryBundle` whose members are plain
in-memory classes backed by Python dicts:

- `InMemoryUsersRepository` — dict of `{user_id: User}` plus a separate dict of
  password hashes; implements `get_by_email`, `get_by_id`,
  `get_password_hash`, `list_all`, `create`, `set_active`,
  `set_password_hash`, `set_google_identity`, and `ensure_admin`.
- `InMemoryGoogleTokensRepository` — `get_for_user` / `save_for_user`.
- `InMemoryDeepgramCredentialsRepository` — stores the **already-encrypted** key
  string (`get_encrypted_for_user` / `save_for_user`).
- `InMemoryDriveSettingsRepository` — `get_for_user` / `save_for_user`.
- `InMemoryTranscriptionJobsRepository` — `create_job`, `list_jobs_for_user`,
  `find_active_for_user`, with a monotonic integer sequence and a fixed
  timestamp (`_FIXED_TS = "2026-06-03T00:00:00+00:00"`) so assertions are
  deterministic.

These fakes implement the same method names the PostgreSQL adapters expose, so a
route that works against the fake works against `app/repositories/postgres.py`
and `app/web/repositories.py` unchanged.

### Worker side (`tests/support.py`)

The worker is assembled through `WorkerContainer`, which takes every external
collaborator as a constructor argument. `tests/support.py` provides:

- `drive_file(file_id, name="meeting.mp4")` — builds a `DriveFile` with sane
  defaults.
- `FakeDriveClient` — records `downloaded` / `uploaded` ids, writes dummy
  `b"mp4 bytes"` on `download_by_id`, returns a fake `upload_result`, and can be
  told to `fail_download` / `fail_upload` to exercise error paths.
- `FakeDeepgramClient` — returns a canned `results.utterances` response (or a
  custom one), and can `fail` to test `mark_failed`.
- `make_worker_settings(tmp_dir, **overrides)` — a `WorkerSettings` with
  `repository_backend="memory"` and `tmp_dir` pointed at a pytest `tmp_path`.
- `make_worker_container(...)` — wires the container with
  `build_memory_repositories()` (the in-memory `JobRepository` from
  `app/repositories/memory.py`), the fake Drive/Deepgram clients, and the
  transcription seams described below.

Because `WorkerContainer` receives `build_drive_client`,
`build_deepgram_client`, and `credentials_from_token` as lambdas, no real Google
credentials, Drive API, or Deepgram API are ever contacted.

> The worker's `repository_backend="memory"` (`WORKER_REPOSITORY_BACKEND=memory`)
> is, by contract, **dev/tests only**; production uses `postgres`.

## PostgreSQL integration tests (no SQLite)

Persistence tests run against a real PostgreSQL database. The fixtures that
provide it live in `tests/conftest.py`.

### Database URL resolution

The test database URL is resolved by `_test_database_url()` in this exact
precedence order, then normalized through `normalize_database_url(...)`:

1. `TEST_DATABASE_URL`
2. `DATABASE_URL`
3. `DEFAULT_TEST_URL` =
   `postgresql+psycopg://meet_user:meet_password@localhost:5432/meet_test`

There is **no SQLite fallback at any tier.** A `psycopg`/PostgreSQL URL is always
used.

### Skip-when-unreachable behavior

The session-scoped `engine` fixture opens a connection to the resolved URL. If
the connection fails for any reason, the fixture calls `pytest.skip(...)` with
the URL and error — the database tests are skipped, **never** rerouted to
SQLite:

```python
try:
    connection = eng.connect()
    connection.close()
except Exception as exc:  # any connection failure means skip.
    eng.dispose()
    pytest.skip(f"PostgreSQL not available at {url!r}: {exc}")
```

This is what lets the unit tier pass on a laptop with no database while the
integration tier only runs where PostgreSQL is present (e.g. CI with a service,
or a local disposable container).

### What the fixtures do

| Fixture | Scope | Purpose |
| --- | --- | --- |
| `engine` | session | Creates the engine, **drops then creates** all `models.Base.metadata` tables, and binds the application's global engine via `db_session.init_engine(engine=eng)` so web routes and background jobs hit the same schema. Drops tables and resets the engine on teardown. |
| `pg` | function | Yields the session engine for tests that manage their own sessions (TestClient, background jobs); `TRUNCATE ... RESTART IDENTITY CASCADE` on every table afterward. |
| `db` | function | Yields a clean ORM session from `db_session.get_sessionmaker()`; rolls back, closes, and truncates all tables afterward. |

Each test that uses `pg` or `db` starts from a clean, empty schema because
`_truncate_all(engine)` runs in the fixture teardown.

## Mocking the local engines (no real model downloads)

Local transcription is the area most at risk of pulling large model files or
shelling out to `ffmpeg` / `whisper-cli`. Tests avoid all of that by injecting
the engine's moving parts. The worker container accepts:

- `transcription_config` — the local-transcription configuration block.
- `transcription_probes` — injectable validation probes (mirrors
  `app/transcription/local_validation.py`), so provider resolution
  (`resolve_provider`) can be tested as valid / invalid / disabled without a
  real model on disk.
- `build_local_provider` — a factory hook so a fake local provider can be
  substituted for the real `faster_whisper`/`whisper.cpp` builders in
  `app/transcription/factory.py`.

Inside the providers, the same idea applies through narrower seams:

- **`model_factory`** — stands in for `faster_whisper.WhisperModel(...)`. In
  production the `faster_whisper` import is lazy and the model is created with
  `device="cpu"`, `compute_type`, `cpu_threads`, `download_root=model_dir`, and
  `local_files_only=not auto_download`. Tests pass a fake `model_factory` that
  returns a stub model yielding canned segments — **nothing is downloaded**.
- **`runner`** — stands in for invoking the `whisper-cli` binary
  (`WHISPER_CPP_BINARY`). Tests inject a fake runner that returns canned
  `-oj` JSON (or stdout/txt fallback) instead of executing the external binary.
- **`audio_extractor`** — stands in for the `ffmpeg` call in
  `app/transcription/audio.py` that produces a 16 kHz mono WAV. Tests inject a
  fake extractor so `ffmpeg` is never required.

This is why **real models are never downloaded in tests**: the only code paths
that would fetch or load a model, or shell out to `ffmpeg`/`whisper-cli`, are
behind these injectable seams. The provider/normalizer logic
(`app/transcription/normalizer.py`, the normalized transcript schema, and
`render_local_text`) is tested against the fakes' canned output, and
`LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` (default `false`) is never exercised against
the network.

A test also enforces that **secrets are never logged**, keeping per-user
Deepgram keys and Google tokens out of log output.

## Running the suite

The canonical validation commands from `CLAUDE.md`:

```bash
python -m pytest -v
python -m compileall app scripts
```

To run the full suite (verbose):

```bash
python -m pytest -v
```

To run quietly, the way CI does:

```bash
python -m pytest -q
```

Common scoped invocations:

```bash
# A single file
python -m pytest tests/test_worker_flow.py -v

# A single test by node id
python -m pytest tests/test_worker_flow.py::test_process_marks_completed -v

# Filter by keyword
python -m pytest -k "queue and not redis" -v
```

With **no** `TEST_DATABASE_URL` / `DATABASE_URL` reachable, the unit tier passes
and the PostgreSQL tier is reported as **skipped** — that is expected and not a
failure.

## Running the PostgreSQL tests with a disposable container

The recommended way to exercise the integration tier locally is a throwaway
PostgreSQL 16 container, exactly as documented in the `tests/conftest.py`
docstring. The example below maps the container to host port `55432` to avoid
clashing with any local PostgreSQL on `5432`:

```bash
docker run -d --name meet_pg_test \
    -e POSTGRES_DB=meet_test \
    -e POSTGRES_USER=meet_user \
    -e POSTGRES_PASSWORD=meet_password \
    -p 55432:5432 \
    postgres:16

export TEST_DATABASE_URL=postgresql+psycopg://meet_user:meet_password@localhost:55432/meet_test

python -m pytest -v
```

Tear the container down when finished:

```bash
docker rm -f meet_pg_test
```

Notes:

- The `engine` fixture **drops and recreates** the schema, and the `pg`/`db`
  fixtures **truncate** every table between tests, so the database is fully
  managed for you — point `TEST_DATABASE_URL` at a database you do not mind being
  wiped (a disposable container is ideal). Do **not** point it at any database
  holding real data.
- The URL **must** be a PostgreSQL DSN
  (`postgresql+psycopg://user:pass@host:port/db`). A SQLite URL is never valid
  here.
- If you already run the project's PostgreSQL via `docker compose`, you can point
  `TEST_DATABASE_URL` at a **separate** test database on it — but never at the
  application database, since the fixtures drop/truncate tables.

## How CI runs the tests

`.github/workflows/docker-publish.yml` defines a `test` job that the `publish`
job depends on (`needs: test`). The workflow triggers on push to `main` and
`integration/postgres-platform`, on pull requests to `main`, and via
`workflow_dispatch`. The `test` job:

1. `actions/setup-python@v5` with Python `3.11`.
2. `pip install -r requirements.txt`.
3. `python -m compileall app scripts`.
4. `python -m pytest -q`.

Only if `test` succeeds does `publish` build and push the image to GHCR
(`ghcr.io/gabedsam01/meet-transcription`) with the `:latest` and `:<short-sha>`
tags. Because the CI `test` job does not provision a PostgreSQL service, the
PostgreSQL integration tier is **skipped** there under the
skip-when-unreachable rule, while the unit tier runs in full. Run the
PostgreSQL tier locally with a disposable container (above) when changing the
database, the repository adapters, or the Alembic migration.
