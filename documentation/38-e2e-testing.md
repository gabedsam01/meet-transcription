# End-to-End Testing

This document explains the project's end-to-end (E2E) tier: what it covers, the
deliberate choice **not** to drive a real browser, how the app and worker are
wired together through in-memory fakes, and how to add a scenario. The E2E tests
live under `tests/e2e/` and assert the behaviors an operator actually cares about
— login, onboarding, run-once, a worker that completes jobs out of band,
downloads/exports, friendly errors, and resilience when Redis is down.

See also: [Testing](18-testing.md) for the broader suite and PostgreSQL
integration rules, [Development](17-development.md) for the local workflow, and
[Observability](34-observability.md) for the `/health`, `/ready`, and `/version`
endpoints these tests exercise.

## The approach: integrated E2E, not a browser

These are **integrated E2E** tests: they drive the *real* FastAPI app through
`fastapi.testclient.TestClient` and the *real* `JobProcessor`
(`app/worker/processor.py`), with every external dependency swapped for an
in-memory fake. There is **no Playwright, no Selenium, no headless browser, and
no build step**. The module docstring in `tests/e2e/helpers.py` calls this the
"E2E integrado" approach: a logged-in admin clicks through onboarding /
run-once / download while a fake worker processes jobs out of band, exactly as
the two services would in production.

Why this and not a browser harness:

- **The UI is server-rendered Jinja2, not an SPA.** Per the project's hard
  rules, there is no React and no CDN/asset build. Every page is HTML returned by
  a route, so assertions are plain substring checks against `response.text` —
  a browser would add cost without adding coverage.
- **No browser, no build, no flake.** A browser harness needs a runtime, a
  driver, and a built front end. The server-rendered HTML needs none of that, so
  the E2E tier runs in the same `pytest` process as the unit tier, on a laptop or
  in CI, in milliseconds.
- **Deterministic and fast through fakes.** Drive, Deepgram, the queue, and the
  worker repositories are dict-backed fakes with fixed timestamps and monotonic
  ids. No network, no real model download, no clock dependence — the same inputs
  always produce the same HTML and the same job rows.
- **Production-faithful where it matters.** The fakes implement the *same*
  contracts as the real adapters (the `JobRepository` Protocol in
  `app/core/ports.py`, the `RepositoryBundle` in `app/web/repositories.py`, the
  `TranscriptionQueue` port). A route or processor that passes against the fake
  passes against PostgreSQL/Redis unchanged. This honors the hard constraints:
  **PostgreSQL is the single source of truth (no SQLite, even in tests)**, and
  **the web layer never transcribes in-request** — transcription only happens in
  the `JobProcessor`, driven by the fake worker.

## How the app and worker are wired

All wiring goes through `tests/e2e/helpers.py`, which composes the builders from
`tests/fakes.py` (web/auth bundle) and `tests/support.py` (worker side).

### The two repository bundles

`create_app` (`app/web/main.py`) holds **two** repository bundles over the same
data, and `build_app(...)` in `helpers.py` lets a scenario seed either:

| Bundle | Builder | What it backs |
| --- | --- | --- |
| **auth** | `build_fake_repositories()` (`tests/fakes.py`) → `RepositoryBundle` | Login, the dashboard/onboarding Google + Drive + Deepgram status. Members: `InMemoryUsersRepository`, `InMemoryGoogleTokensRepository`, `InMemoryDeepgramCredentialsRepository`, `InMemoryDriveSettingsRepository`, `InMemoryTranscriptionJobsRepository`. |
| **worker** | `build_memory_repositories()` (`app/repositories/memory.py`) → `Repositories` | Jobs and transcripts shown in `/jobs`, `/jobs/{id}`, `/search`, and processed by the worker. Members: `InMemoryJobRepository`, `InMemoryTranscriptRepository`, `InMemorySettingsRepository`, `InMemoryGoogleTokenRepository`. |

Login bootstraps the admin as **user id=1** (`ADMIN_ID` in `helpers.py`, via
`ensure_admin`). Seed only the bundle a scenario needs:

- `seed_worker_ready(worker, ...)` — sets `worker.settings` (source/dest folder,
  save-copy, per-user Deepgram key) and a worker-side `GoogleToken`, so
  `/jobs/run-once` and the processor have everything they need.
- `seed_auth_connected(auth, ...)` — saves an auth-side `GoogleToken` and
  `DriveSettings` so onboarding shows Google + Drive ready.
- `seed_deepgram_key(auth, value=...)` — stores the **already-encrypted** key
  blob (the fake never encrypts/decrypts; secrets are never logged or echoed).

### The injected fakes

`build_app(tmp_path, *, auth, worker, queue, transcription_status, drive)` injects:

- **`InMemoryTranscriptionQueue`** (`app/queue/memory_queue.py`) for the Redis
  path — `queued_job_ids()` lets a test assert the run-once enqueue happened.
  Redis is the **queue/lock only**; the job row in the worker bundle is the
  source of truth.
- **`FakeDriveClient`** (`tests/support.py`) — wired via
  `app.state.build_drive_client`; lists canned `drive_file(...)` videos, writes
  dummy `b"mp4 bytes"` on download, returns a fake upload id, and can
  `fail_download` / `fail_upload`.
- **`FakeDeepgramClient`** (`tests/support.py`) — returns a canned
  `results.utterances` payload (`"Ola mundo."`) and can `fail=True` to drive the
  failed-job path.
- **`transcription_status`** — a `ProviderStatus` fake mirroring
  `get_transcription_provider_status`. Three postures are provided:
  `deepgram_required_status()`, `local_invalid_status(doc_url)`, and
  `local_valid_status()`. The Portuguese `message` strings (e.g. `"Modelo local
  inválido. Consulte a documentação de modelos locais."`) are what the UI renders.

### The fake worker (`run_worker_once`)

A real browser test would need a running worker process. Instead,
`run_worker_once(tmp_path, worker, *, drive, deepgram, ..., webhook_notifier)`
builds a real `WorkerContainer` via `make_worker_container` and the **real**
`JobProcessor`, then drains every pending job out of band:

```python
processor = JobProcessor(container)
while True:
    job = worker.jobs.claim_next_pending_job("e2e-worker", now())
    if job is None:
        break
    processor.process(job)
```

This is the production claim→process loop with fake collaborators. The web
request only created a `pending` job and enqueued its id; the fake worker is what
moves it to `completed`/`failed` — proving the web layer never transcribes
in-request.

## Scenarios actually covered

| File | Scenario |
| --- | --- |
| `test_health_ready_version_e2e.py` | `/health` is public + minimal (`{"status": "ok"}`); `/ready` is ready in poll mode (database/migrations/queue checks `ok`); `/ready` degrades to **503** when the worker backend is unavailable; `/version` reports build + provider posture (`deepgram_required`, `queue_backend`, `summaries_enabled`). |
| `test_onboarding_e2e.py` | Fresh user sees the incomplete checklist (`"Conectar Google"`, `"Configurar pasta"`, `"Em configuração"`); fully-seeded user sees all green (`"Tudo pronto"`, `"Automação ativa"`); `/onboarding` redirects to `/login` when not authenticated. |
| `test_job_lifecycle_e2e.py` | Admin logs in, run-once scans Drive and enqueues **one** pending job (asserted via `queue.queued_job_ids()`), the fake worker completes it (`badge-completed`), then download of the TXT plus the SRT/VTT/JSON/MD exports works and an unsupported `format=pdf` returns **400**; the job detail page lists the export links (`"Exportar como"`, `?format=srt`, `?format=json`). |
| `test_resilience_e2e.py` | Run-once blocked without a Deepgram key shows the friendly `"Configure sua Deepgram API Key"` and creates **no** job; **Redis unavailable** (`BrokenQueue`) keeps the job `pending` (Postgres is source of truth), warns `"Fila indisponível"`, and degrades `/ready` to 503 with `queue.ok=False`; local-invalid posture renders `"Modelo local inválido"` plus the docs link; a failed transcription becomes a dead-letter job shown as `badge-failed` with the safe message `"deepgram failed"` and **no** `Traceback`. |
| `test_search_e2e.py` | Full-text search returns the user's transcript with a snippet and a `/jobs/{id}` link; it does **not** leak another user's transcript (`"Nenhuma transcrição encontrada"`); an empty query renders the form (`"Buscar transcrições"`). |
| `test_webhooks_e2e.py` | `job.completed` webhook is delivered and **retried after a 429** (two transport calls); `job.failed` carries a safe `error_message`; webhook failures **never** block job completion (job still reaches `completed`); disabled webhooks call nothing. |
| `test_error_pages_e2e.py` | A browser 404 renders friendly HTML (`"Algo deu errado"`, `"Voltar ao início"`, no `Traceback`); an API 404 stays JSON; job-detail and download 404s stay safe (no `Traceback`). |

Every assertion is a substring check against rendered HTML/JSON or a job-row
state — no DOM, no screenshots. The recurring `assert "Traceback" not in ...`
checks enforce the hard rule that **tracebacks and secrets never reach the UI**.

## Running the E2E tier

The E2E tests need no database and no services — they run with the unit tier:

```bash
.venv/bin/python -m pytest -q tests/e2e/
```

Scoped runs:

```bash
# One file
.venv/bin/python -m pytest tests/e2e/test_job_lifecycle_e2e.py -v

# One scenario
.venv/bin/python -m pytest \
  "tests/e2e/test_resilience_e2e.py::test_redis_unavailable_keeps_job_pending_and_warns_and_degrades_ready" -v
```

## PostgreSQL integration tests skip when no DB

The E2E tier above is database-free. The separate **PostgreSQL integration**
tests (the repository adapters, schema, migrations) require a real database and
**skip** when none is reachable — they never fall back to SQLite. The
session-scoped `engine` fixture in `tests/conftest.py` opens a connection to the
resolved URL and calls `pytest.skip(...)` on any failure:

```python
except Exception as exc:  # any connection failure means skip.
    eng.dispose()
    pytest.skip(f"PostgreSQL not available at {url!r}: {exc}")
```

The URL is resolved by `_test_database_url()` in precedence
`TEST_DATABASE_URL` → `DATABASE_URL` → `DEFAULT_TEST_URL`
(`postgresql+psycopg://meet_user:meet_password@localhost:5432/meet_test`). To run
them, point `TEST_DATABASE_URL` at a disposable PostgreSQL 16 — see
[Testing](18-testing.md) for the full container recipe.

## Adding a new scenario

The helpers make a new scenario a few lines. The pattern:

1. **Build the bundles you need.** `build_memory_repositories()` for the worker
   side; `build_fake_repositories()` for the auth side (only when asserting
   onboarding/dashboard status).
2. **Seed.** `seed_worker_ready(worker, ...)`, `seed_auth_connected(auth, ...)`,
   and/or `seed_deepgram_key(auth, ...)`. Set `deepgram_key=None` to drive the
   "missing key" path; pass `fail=True` to `FakeDeepgramClient`/`fail_download`
   to `FakeDriveClient` to drive failures.
3. **Build the app and pick the provider posture.** `build_app(tmp_path,
   worker=..., queue=..., transcription_status=..., drive=...)` — choose
   `deepgram_required_status()`, `local_invalid_status(...)`, or
   `local_valid_status()`.
4. **Drive it with `TestClient`.** `login(client)`, then POST/GET the routes.
5. **Process jobs out of band** with `run_worker_once(tmp_path, worker,
   drive=..., deepgram=..., webhook_notifier=...)`.
6. **Assert on HTML/JSON or job state.** Quote Portuguese UI strings verbatim,
   and add `assert "Traceback" not in body` for any error path.

```python
def test_local_engine_completes_a_job(tmp_path):
    worker = build_memory_repositories()
    seed_worker_ready(worker, deepgram_key=None)  # local path needs no Deepgram key
    drive = FakeDriveClient(files=[drive_file("file-1", "Standup.mp4")])
    app = build_app(
        tmp_path, worker=worker,
        transcription_status=local_valid_status(), drive=drive,
    )
    with TestClient(app) as client:
        login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        run_worker_once(tmp_path, worker, drive=drive,
                        build_local_provider=lambda *_: my_fake_provider)
        assert "badge-completed" in client.get("/jobs").text
```

If a scenario needs a method the in-memory `JobRepository` does not yet expose,
add it to **both** adapters (`app/repositories/memory.py` and
`app/repositories/postgres.py`) and the `runtime_checkable` Protocol, and update
`tests/test_core_ports.py::_Stub` — keep the contract in lockstep, exactly as the
`JobRepository` rules in `CLAUDE.md` require.
