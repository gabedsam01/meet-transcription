# PostgreSQL Multiuser Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone PostgreSQL-backed multiuser transcription worker — UI creates `pending` jobs, the worker claims them safely with `FOR UPDATE SKIP LOCKED` semantics, transcribes via each user's own Deepgram key, and persists the transcript in the DB for download.

**Architecture:** Ports & adapters (hexagonal). New code depends only on domain dataclasses and repository Protocols. An in-memory adapter (shipped, also the test fake, dev/test only) and a PostgreSQL adapter (delivered later by `feat/postgres-core`) implement the ports. No `sqlite3`, `SQLAlchemy`, or `psycopg` in this branch's new code.

**Tech Stack:** Python 3.11, dataclasses + `typing.Protocol`, `threading`, FastAPI (existing web app), pytest. Reuses the existing `DriveClient`, `DeepgramClient`, `format_transcript`, `sanitize_filename`.

---

## Execution conventions

- **Work in the worktree:** all paths are under `/home/gabedsam01/Documentos/meet-transcription-worker` (branch `feat/postgres-worker`). Run git as `git -C /home/gabedsam01/Documentos/meet-transcription-worker ...`.
- **Commit trailer:** every commit message ends with a blank line then:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **No push.**
- **Run tests** from the worktree root: `cd /home/gabedsam01/Documentos/meet-transcription-worker && python -m pytest ...`.
- **Backend rule (mandatory):** `WORKER_REPOSITORY_BACKEND` defaults to `postgres`; `memory` is dev/test only and forbidden in production; selecting `postgres` before `feat/postgres-core` is integrated must fail with a clear, actionable error.

## File structure

Created:
- `app/core/__init__.py` — package marker.
- `app/core/models.py` — `Job`, `Settings`, `GoogleToken`, `Transcript`, `JobStatus`.
- `app/core/ports.py` — repository Protocols + `Repositories` bundle + contract docstrings.
- `app/repositories/__init__.py` — `build_repositories(backend)` factory + `RepositoryBackendError`.
- `app/repositories/memory.py` — in-memory adapters + `build_memory_repositories()`.
- `app/services/__init__.py` — package marker.
- `app/services/job_service.py` — `create_next_pending_job(...)`.
- `app/services/download_service.py` — `get_downloadable_transcript(...)`.
- `app/google_auth.py` — `build_oauth_credentials`, `credentials_from_token`.
- `app/worker/__init__.py` — package marker.
- `app/worker/config.py` — `WorkerSettings`.
- `app/worker/container.py` — `WorkerContainer`, `build_container()`.
- `app/worker/processor.py` — `JobProcessor`.
- `app/worker/loop.py` — `run_worker_loop(...)`.
- `app/worker/main.py` — entrypoint + stale recovery.
- `tests/support.py` — shared fakes + container builders for tests.
- `tests/test_core_models.py`, `tests/test_core_ports.py`, `tests/test_repositories_memory.py`, `tests/test_repositories_factory.py`, `tests/test_google_auth.py`, `tests/test_worker_config.py`, `tests/test_worker_container.py`, `tests/test_worker_processor.py`, `tests/test_worker_loop.py`, `tests/test_worker_main.py`, `tests/test_job_service.py`, `tests/test_download_service.py`, `tests/test_jobs_download_route.py`.

Modified:
- `app/deepgram_client.py` — per-call `api_key` override + `from_api_key`.
- `app/drive_client.py` — `download_by_id`.
- `app/web/main.py` — repositories injection, new `/jobs/run-once` (create pending only), `GET /jobs/{job_id}/download`, jobs/dashboard reads via ports.
- `app/web/templates/jobs.html`, `app/web/templates/dashboard.html` — attribute access, Download button, Drive link.
- `app/web/services.py` — **deleted** (logic moved to `job_service`, `worker`, `google_auth`).
- `tests/test_web_services.py` — **deleted** (replaced by service/route tests).
- `tests/test_web_routes.py` — jobs-related tests rewritten for the new flow.
- `tests/test_deepgram_client.py`, `tests/test_drive_client.py` — add cases.
- `.env.example`, `docker-compose.yml`, `README.md` — worker config, service, docs.

---

## Task 1: Core domain models

**Files:**
- Create: `app/core/__init__.py`, `app/core/models.py`
- Test: `tests/test_core_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_core_models.py`:

```python
from datetime import datetime, timezone

from app.core.models import GoogleToken, Job, JobStatus, Settings, Transcript


def test_job_status_values():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.PROCESSING.value == "processing"
    assert JobStatus.COMPLETED.value == "completed"
    assert JobStatus.FAILED.value == "failed"
    assert JobStatus.SKIPPED.value == "skipped"


def test_job_defaults():
    job = Job(id=1, user_id=7, status=JobStatus.PENDING.value)
    assert job.attempts == 0
    assert job.source_file_id is None
    assert job.transcript_drive_file_id is None


def test_settings_and_token_and_transcript_construct():
    settings = Settings(
        user_id=7, source_drive_folder_id="src", destination_drive_folder_id="dst",
        poll_interval_seconds=300, save_copy_to_drive=True, deepgram_api_key="dg",
    )
    token = GoogleToken(access_token="a", token_uri="u", client_id="c")
    now = datetime.now(timezone.utc)
    transcript = Transcript(
        id=1, job_id=2, user_id=7, text="hello", json_payload={"k": "v"},
        drive_file_id="d", created_at=now,
    )
    assert settings.save_copy_to_drive is True
    assert token.refresh_token is None
    assert transcript.json_payload == {"k": "v"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core'`

- [ ] **Step 3: Write minimal implementation**

`app/core/__init__.py`: empty file.

`app/core/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Job:
    id: int
    user_id: int
    status: str
    source_file_id: str | None = None
    source_file_name: str | None = None
    transcript_drive_file_id: str | None = None
    error_message: str | None = None
    attempts: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    processed_at: datetime | None = None


@dataclass
class Settings:
    user_id: int
    source_drive_folder_id: str
    destination_drive_folder_id: str
    poll_interval_seconds: int
    save_copy_to_drive: bool = False
    deepgram_api_key: str | None = None


@dataclass
class GoogleToken:
    access_token: str
    token_uri: str
    client_id: str
    refresh_token: str | None = None
    client_secret: str | None = None
    scopes: str | None = None
    expiry: str | None = None


@dataclass
class Transcript:
    id: int
    job_id: int
    user_id: int
    text: str
    json_payload: dict[str, Any] | None = None
    drive_file_id: str | None = None
    created_at: datetime | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/core/__init__.py app/core/models.py tests/test_core_models.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add core domain models for worker"
```

---

## Task 2: Repository ports

**Files:**
- Create: `app/core/ports.py`
- Test: `tests/test_core_ports.py`

- [ ] **Step 1: Write the failing test**

`tests/test_core_ports.py`:

```python
from app.core.ports import (
    GoogleTokenRepository,
    JobRepository,
    Repositories,
    SettingsRepository,
    TranscriptRepository,
)


class _Stub:
    # Satisfies the method-name shape of every repository Protocol.
    def claim_next_pending_job(self, *a): ...
    def create_job(self, *a, **k): ...
    def get_job(self, *a): ...
    def mark_completed(self, *a, **k): ...
    def mark_failed(self, *a): ...
    def find_existing_job(self, *a): ...
    def reset_stale_processing_jobs(self, *a): ...
    def list_jobs_for_user(self, *a): ...
    def create(self, *a, **k): ...
    def get_by_job(self, *a): ...
    def get(self, *a): ...


def test_repositories_bundle_holds_four_repos():
    stub = _Stub()
    repos = Repositories(jobs=stub, transcripts=stub, settings=stub, google_tokens=stub)
    assert repos.jobs is stub
    assert repos.google_tokens is stub


def test_protocols_are_runtime_checkable():
    stub = _Stub()
    assert isinstance(stub, JobRepository)
    assert isinstance(stub, TranscriptRepository)
    assert isinstance(stub, SettingsRepository)
    assert isinstance(stub, GoogleTokenRepository)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core_ports.py -v`
Expected: FAIL with `ImportError` / `No module named 'app.core.ports'`

- [ ] **Step 3: Write minimal implementation**

`app/core/ports.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.core.models import GoogleToken, Job, Settings, Transcript


@runtime_checkable
class JobRepository(Protocol):
    def claim_next_pending_job(self, worker_id: str, now: datetime) -> Job | None:
        """Atomically hand exactly one pending job to one worker.

        Contract (PostgreSQL adapter): inside a single transaction,
            SELECT ... WHERE status='pending' ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED LIMIT 1;
        then mark it 'processing', attempts = attempts + 1, set started_at/updated_at,
        COMMIT, and return it (or None when no pending job is available). Download and
        transcription happen OUTSIDE this transaction.
        """

    def create_job(
        self, user_id: int, source_file_id: str | None,
        source_file_name: str | None, now: datetime,
    ) -> Job: ...

    def get_job(self, job_id: int) -> Job | None: ...

    def mark_completed(
        self, job_id: int, now: datetime, transcript_drive_file_id: str | None = None,
    ) -> None: ...

    def mark_failed(self, job_id: int, error_message: str, now: datetime) -> None: ...

    def find_existing_job(
        self, user_id: int, source_file_id: str, statuses: tuple[str, ...],
    ) -> Job | None:
        """Return a job for (user_id, source_file_id) in any of `statuses`, else None.

        Used to avoid creating a duplicate when one is pending/processing/completed.
        """

    def reset_stale_processing_jobs(
        self, stale_before: datetime, now: datetime,
    ) -> list[Job]:
        """Mark every 'processing' job whose started_at/updated_at < stale_before as
        'failed' with a stale-timeout message; return the affected jobs."""

    def list_jobs_for_user(self, user_id: int) -> list[Job]: ...


@runtime_checkable
class TranscriptRepository(Protocol):
    def create(
        self, job_id: int, user_id: int, text: str,
        json_payload: dict[str, Any] | None, drive_file_id: str | None, now: datetime,
    ) -> Transcript: ...

    def get_by_job(self, job_id: int) -> Transcript | None: ...


@runtime_checkable
class SettingsRepository(Protocol):
    def get(self, user_id: int) -> Settings | None: ...


@runtime_checkable
class GoogleTokenRepository(Protocol):
    def get(self, user_id: int) -> GoogleToken | None: ...


@dataclass
class Repositories:
    jobs: JobRepository
    transcripts: TranscriptRepository
    settings: SettingsRepository
    google_tokens: GoogleTokenRepository
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core_ports.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/core/ports.py tests/test_core_ports.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add repository ports and contracts"
```

---

## Task 3: In-memory repository adapters

**Files:**
- Create: `app/repositories/__init__.py` (empty for now), `app/repositories/memory.py`
- Test: `tests/test_repositories_memory.py`

> Note: `app/repositories/__init__.py` starts empty here and gets the factory in Task 4. Create it empty so `app.repositories.memory` is importable.

- [ ] **Step 1: Write the failing test**

`tests/test_repositories_memory.py`:

```python
import threading
from datetime import datetime, timedelta, timezone

from app.core.models import GoogleToken, JobStatus, Settings
from app.repositories.memory import build_memory_repositories


def _now():
    return datetime.now(timezone.utc)


def test_create_job_is_pending_with_zero_attempts():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    assert job.status == JobStatus.PENDING.value
    assert job.attempts == 0
    assert job.source_file_id == "src-1"


def test_claim_marks_processing_increments_attempts_and_is_one_shot():
    repos = build_memory_repositories()
    repos.jobs.create_job(7, "src-1", "a.mp4", _now())

    claimed = repos.jobs.claim_next_pending_job("w1", _now())
    assert claimed.status == JobStatus.PROCESSING.value
    assert claimed.attempts == 1
    assert claimed.started_at is not None
    # No more pending jobs -> second claim returns None.
    assert repos.jobs.claim_next_pending_job("w2", _now()) is None


def test_claim_is_fifo_by_creation():
    repos = build_memory_repositories()
    first = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.create_job(7, "src-2", "b.mp4", _now())
    claimed = repos.jobs.claim_next_pending_job("w1", _now())
    assert claimed.id == first.id


def test_concurrent_claims_never_hand_out_the_same_job():
    repos = build_memory_repositories()
    for i in range(20):
        repos.jobs.create_job(7, f"src-{i}", f"{i}.mp4", _now())

    claimed_ids = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        while True:
            job = repos.jobs.claim_next_pending_job("w", _now())
            if job is None:
                return
            with lock:
                claimed_ids.append(job.id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_ids) == 20
    assert len(set(claimed_ids)) == 20  # no job claimed twice


def test_mark_completed_and_failed():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.mark_completed(job.id, _now(), transcript_drive_file_id="drive-1")
    done = repos.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert done.transcript_drive_file_id == "drive-1"
    assert done.processed_at is not None

    other = repos.jobs.create_job(7, "src-2", "b.mp4", _now())
    repos.jobs.mark_failed(other.id, "boom", _now())
    failed = repos.jobs.get_job(other.id)
    assert failed.status == JobStatus.FAILED.value
    assert failed.error_message == "boom"


def test_find_existing_job_filters_by_status():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    assert repos.jobs.find_existing_job(7, "src-1", ("pending",)).id == job.id
    assert repos.jobs.find_existing_job(7, "src-1", ("completed",)) is None
    assert repos.jobs.find_existing_job(99, "src-1", ("pending",)) is None


def test_reset_stale_processing_jobs():
    repos = build_memory_repositories()
    fresh = repos.jobs.create_job(7, "src-fresh", "f.mp4", _now())
    stale = repos.jobs.create_job(7, "src-stale", "s.mp4", _now())
    pending = repos.jobs.create_job(7, "src-pending", "p.mp4", _now())
    repos.jobs.claim_next_pending_job("w", _now())  # claims `fresh` (FIFO)
    # Force `stale` into processing with an old started_at.
    repos.jobs.claim_next_pending_job("w", _now())  # claims `stale`
    old = _now() - timedelta(hours=3)
    repos.jobs._jobs[stale.id].started_at = old  # test reaches into the fake

    reset = repos.jobs.reset_stale_processing_jobs(_now() - timedelta(hours=1), _now())

    assert [j.id for j in reset] == [stale.id]
    assert repos.jobs.get_job(stale.id).status == JobStatus.FAILED.value
    assert "stale" in repos.jobs.get_job(stale.id).error_message
    assert repos.jobs.get_job(fresh.id).status == JobStatus.PROCESSING.value
    assert repos.jobs.get_job(pending.id).status == JobStatus.PENDING.value


def test_transcript_create_and_get_by_job():
    repos = build_memory_repositories()
    repos.transcripts.create(5, 7, "hello", {"k": "v"}, "drive-1", _now())
    got = repos.transcripts.get_by_job(5)
    assert got.text == "hello"
    assert got.json_payload == {"k": "v"}
    assert repos.transcripts.get_by_job(999) is None


def test_settings_and_token_seed_and_get():
    repos = build_memory_repositories()
    repos.settings.set(Settings(7, "src", "dst", 300, True, "dg-key"))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))
    assert repos.settings.get(7).deepgram_api_key == "dg-key"
    assert repos.google_tokens.get(7).access_token == "a"
    assert repos.settings.get(999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_memory.py -v`
Expected: FAIL with `No module named 'app.repositories'`

- [ ] **Step 3: Write minimal implementation**

`app/repositories/__init__.py`: empty file (factory added in Task 4).

`app/repositories/memory.py`:

```python
from __future__ import annotations

import dataclasses
import threading
from datetime import datetime
from typing import Any

from app.core.models import GoogleToken, Job, JobStatus, Settings, Transcript
from app.core.ports import Repositories

ACTIVE_STATUSES = (JobStatus.PENDING.value, JobStatus.PROCESSING.value)


def _copy(obj):
    return dataclasses.replace(obj) if obj is not None else None


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[int, Job] = {}
        self._seq = 0

    def create_job(self, user_id, source_file_id, source_file_name, now) -> Job:
        with self._lock:
            self._seq += 1
            job = Job(
                id=self._seq, user_id=user_id, status=JobStatus.PENDING.value,
                source_file_id=source_file_id, source_file_name=source_file_name,
                attempts=0, created_at=now, updated_at=now,
            )
            self._jobs[job.id] = job
            return _copy(job)

    def claim_next_pending_job(self, worker_id, now) -> Job | None:
        with self._lock:
            pending = sorted(
                (j for j in self._jobs.values() if j.status == JobStatus.PENDING.value),
                key=lambda j: j.id,
            )
            if not pending:
                return None
            job = pending[0]
            job.status = JobStatus.PROCESSING.value
            job.attempts += 1
            job.started_at = now
            job.updated_at = now
            return _copy(job)

    def get_job(self, job_id) -> Job | None:
        with self._lock:
            return _copy(self._jobs.get(job_id))

    def mark_completed(self, job_id, now, transcript_drive_file_id=None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.COMPLETED.value
            job.processed_at = now
            job.updated_at = now
            if transcript_drive_file_id is not None:
                job.transcript_drive_file_id = transcript_drive_file_id

    def mark_failed(self, job_id, error_message, now) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.FAILED.value
            job.error_message = error_message
            job.updated_at = now

    def find_existing_job(self, user_id, source_file_id, statuses) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if (
                    job.user_id == user_id
                    and job.source_file_id == source_file_id
                    and job.status in statuses
                ):
                    return _copy(job)
            return None

    def reset_stale_processing_jobs(self, stale_before, now) -> list[Job]:
        with self._lock:
            reset: list[Job] = []
            for job in self._jobs.values():
                if job.status != JobStatus.PROCESSING.value:
                    continue
                marker = job.started_at or job.updated_at
                if marker is not None and marker < stale_before:
                    job.status = JobStatus.FAILED.value
                    job.error_message = "stale timeout: job exceeded processing window"
                    job.updated_at = now
                    reset.append(_copy(job))
            return reset

    def list_jobs_for_user(self, user_id) -> list[Job]:
        with self._lock:
            jobs = [_copy(j) for j in self._jobs.values() if j.user_id == user_id]
            jobs.sort(key=lambda j: j.id, reverse=True)
            return jobs


class InMemoryTranscriptRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_job: dict[int, Transcript] = {}
        self._seq = 0

    def create(self, job_id, user_id, text, json_payload, drive_file_id, now) -> Transcript:
        with self._lock:
            self._seq += 1
            transcript = Transcript(
                id=self._seq, job_id=job_id, user_id=user_id, text=text,
                json_payload=json_payload, drive_file_id=drive_file_id, created_at=now,
            )
            self._by_job[job_id] = transcript
            return _copy(transcript)

    def get_by_job(self, job_id) -> Transcript | None:
        with self._lock:
            return _copy(self._by_job.get(job_id))


class InMemorySettingsRepository:
    def __init__(self) -> None:
        self._settings: dict[int, Settings] = {}

    def set(self, settings: Settings) -> None:  # test/dev seeding helper
        self._settings[settings.user_id] = settings

    def get(self, user_id) -> Settings | None:
        return _copy(self._settings.get(user_id))


class InMemoryGoogleTokenRepository:
    def __init__(self) -> None:
        self._tokens: dict[int, GoogleToken] = {}

    def set(self, user_id: int, token: GoogleToken) -> None:  # test/dev seeding helper
        self._tokens[user_id] = token

    def get(self, user_id) -> GoogleToken | None:
        return _copy(self._tokens.get(user_id))


def build_memory_repositories() -> Repositories:
    return Repositories(
        jobs=InMemoryJobRepository(),
        transcripts=InMemoryTranscriptRepository(),
        settings=InMemorySettingsRepository(),
        google_tokens=InMemoryGoogleTokenRepository(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_memory.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/repositories/__init__.py app/repositories/memory.py tests/test_repositories_memory.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add in-memory repository adapters"
```

---

## Task 4: Repository factory and backend selection

**Files:**
- Modify: `app/repositories/__init__.py`
- Test: `tests/test_repositories_factory.py`

- [ ] **Step 1: Write the failing test**

`tests/test_repositories_factory.py`:

```python
import pytest

from app.repositories import RepositoryBackendError, build_repositories
from app.repositories.memory import InMemoryJobRepository


def test_memory_backend_builds_in_memory_repositories():
    repos = build_repositories("memory")
    assert isinstance(repos.jobs, InMemoryJobRepository)


def test_default_backend_is_postgres_and_fails_clearly_when_not_integrated():
    # No backend argument -> default 'postgres'. On this branch the Postgres
    # adapter does not exist yet, so it must fail with a clear, actionable error.
    with pytest.raises(RepositoryBackendError) as exc:
        build_repositories(None)
    message = str(exc.value)
    assert "postgres-core" in message
    assert "memory" in message  # mentions the dev-only escape hatch


def test_explicit_postgres_backend_fails_clearly():
    with pytest.raises(RepositoryBackendError):
        build_repositories("postgres")


def test_unknown_backend_is_rejected():
    with pytest.raises(RepositoryBackendError):
        build_repositories("mysql")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories_factory.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_repositories'`

- [ ] **Step 3: Write minimal implementation**

`app/repositories/__init__.py`:

```python
from __future__ import annotations

from app.core.ports import Repositories


class RepositoryBackendError(RuntimeError):
    """Raised when the requested repository backend cannot be built."""


def build_repositories(backend: str | None = None) -> Repositories:
    """Build the repository bundle for the selected backend.

    Default is 'postgres' (production). 'memory' is for tests, local smoke runs
    and development only, and is forbidden in production.
    """
    selected = (backend or "postgres").strip().lower() or "postgres"

    if selected == "memory":
        from app.repositories.memory import build_memory_repositories

        return build_memory_repositories()

    if selected == "postgres":
        try:
            from app.repositories.postgres import build_postgres_repositories
        except ImportError as exc:
            raise RepositoryBackendError(
                "WORKER_REPOSITORY_BACKEND=postgres but the PostgreSQL adapter is not "
                "available on this branch. The real repositories are delivered by "
                "feat/postgres-core; merge/integrate that branch before running against "
                "PostgreSQL. For local development only (never production) set "
                "WORKER_REPOSITORY_BACKEND=memory."
            ) from exc
        return build_postgres_repositories()

    raise RepositoryBackendError(
        f"Unknown WORKER_REPOSITORY_BACKEND={backend!r}; use 'postgres' (default) "
        f"or 'memory' (development/tests only)."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories_factory.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/repositories/__init__.py tests/test_repositories_factory.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add repository factory and backend selection"
```

---

## Task 5: DeepgramClient per-user key

**Files:**
- Modify: `app/deepgram_client.py`
- Test: `tests/test_deepgram_client.py` (add cases; do not change existing ones)

- [ ] **Step 1: Write the failing test (append to `tests/test_deepgram_client.py`)**

```python
def test_from_api_key_builds_client_with_options():
    client = DeepgramClient.from_api_key("user-key", model="nova-3", language="pt-BR")
    assert client.api_key == "user-key"
    assert client.model == "nova-3"


def test_transcribe_per_call_api_key_overrides_instance_key(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(200, {"results": {}}))
    client = DeepgramClient.from_api_key("instance-key", session=session)

    client.transcribe(video, api_key="per-call-key")

    assert session.requests[0]["headers"]["Authorization"] == "Token per-call-key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deepgram_client.py -v`
Expected: FAIL with `AttributeError: type object 'DeepgramClient' has no attribute 'from_api_key'`

- [ ] **Step 3: Write minimal implementation**

In `app/deepgram_client.py`, add the classmethod (next to `from_settings`) and change `transcribe`:

```python
    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        model: str = "nova-3",
        language: str = "pt-BR",
        smart_format: bool = True,
        punctuate: bool = True,
        diarize: bool = True,
        utterances: bool = True,
        session: Any | None = None,
    ) -> "DeepgramClient":
        return cls(
            api_key=api_key, model=model, language=language,
            smart_format=smart_format, punctuate=punctuate, diarize=diarize,
            utterances=utterances, session=session,
        )

    def transcribe(self, video_path: str | Path, api_key: str | None = None) -> dict[str, Any]:
        key = api_key or self.api_key
        if not key:
            raise DeepgramError("Deepgram API key is required")
        path = Path(video_path)
        with path.open("rb") as video_file:
            response = self.session.post(
                self.endpoint,
                headers={
                    "Authorization": f"Token {key}",
                    "Content-Type": "video/mp4",
                },
                params=self._params(),
                data=video_file,
                timeout=self.timeout,
            )

        if not 200 <= response.status_code < 300:
            raise DeepgramError(
                f"Deepgram request failed with status {response.status_code}: "
                f"{response.text}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise DeepgramError("Deepgram returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DeepgramError("Deepgram returned an unexpected JSON payload")
        return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deepgram_client.py -v`
Expected: PASS (4 tests — 2 existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/deepgram_client.py tests/test_deepgram_client.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "support per-user api key in DeepgramClient"
```

---

## Task 6: DriveClient download_by_id

**Files:**
- Modify: `app/drive_client.py`
- Test: `tests/test_drive_client.py` (add cases)

- [ ] **Step 1: Write the failing test (append to `tests/test_drive_client.py`)**

```python
def test_download_by_id_writes_media_to_destination(tmp_path, monkeypatch):
    import app.drive_client as drive_module
    from app.drive_client import DriveClient

    class FakeChunkDownloader:
        def __init__(self, handle, request):
            self.handle = handle
            self.request = request
            self.done = False

        def next_chunk(self):
            self.handle.write(b"video-bytes")
            self.done = True
            return None, True

    monkeypatch.setattr(drive_module, "MediaIoBaseDownload", FakeChunkDownloader, raising=False)

    captured = {}

    class FakeFiles:
        def get_media(self, fileId, supportsAllDrives):
            captured["file_id"] = fileId
            captured["all_drives"] = supportsAllDrives
            return "request-object"

    class FakeService:
        def files(self):
            return FakeFiles()

    client = DriveClient.__new__(DriveClient)
    client.service = FakeService()
    destination = tmp_path / "out" / "video.mp4"

    client.download_by_id("file-123", destination)

    assert captured == {"file_id": "file-123", "all_drives": True}
    assert destination.read_bytes() == b"video-bytes"
```

> Note: the implementation imports `MediaIoBaseDownload` at module top so the monkeypatch can replace it. Add `from googleapiclient.http import MediaIoBaseDownload` to the module imports inside Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_client.py::test_download_by_id_writes_media_to_destination -v`
Expected: FAIL with `AttributeError: 'DriveClient' object has no attribute 'download_by_id'`

- [ ] **Step 3: Write minimal implementation**

In `app/drive_client.py`, add a module-level import near the top (after the existing imports):

```python
from googleapiclient.http import MediaIoBaseDownload
```

Add `download_by_id` and refactor `download_file` to reuse it:

```python
    def download_by_id(self, file_id: str, destination: str | Path) -> None:
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with destination_path.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

    def download_file(self, file: DriveFile, destination: str | Path) -> None:
        self.download_by_id(file.id, destination)
```

Remove the now-duplicated body (and the local `from googleapiclient.http import MediaIoBaseDownload`) from the old `download_file`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_client.py -v`
Expected: PASS (all existing + new)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/drive_client.py tests/test_drive_client.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add download_by_id to DriveClient"
```

---

## Task 7: Shared google_auth helper

**Files:**
- Create: `app/google_auth.py`
- Modify: `app/web/services.py` (import the shared helper — temporary; services.py is deleted in Task 15)
- Test: `tests/test_google_auth.py`

- [ ] **Step 1: Write the failing test**

`tests/test_google_auth.py`:

```python
from app.core.models import GoogleToken
from app.google_auth import build_oauth_credentials, credentials_from_token


def test_build_oauth_credentials_maps_web_token_format():
    credentials = build_oauth_credentials(
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": "https://www.googleapis.com/auth/drive",
            "expiry": "2026-06-03T00:00:00+00:00",
        }
    )
    assert credentials.token == "access-token"
    assert credentials.refresh_token == "refresh-token"


def test_credentials_from_token_uses_domain_object():
    token = GoogleToken(
        access_token="access-token", token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id", refresh_token="refresh-token",
        client_secret="client-secret",
        scopes="https://www.googleapis.com/auth/drive",
        expiry="2026-06-03T00:00:00+00:00",
    )
    credentials = credentials_from_token(token)
    assert credentials.token == "access-token"
    assert credentials.refresh_token == "refresh-token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_google_auth.py -v`
Expected: FAIL with `No module named 'app.google_auth'`

- [ ] **Step 3: Write minimal implementation**

`app/google_auth.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import GoogleToken
from app.drive_client import DRIVE_SCOPES


def build_oauth_credentials(token: dict):
    """Build google.oauth2 Credentials from a stored web-OAuth token dict."""
    from google.oauth2.credentials import Credentials

    scopes = token.get("scopes") or DRIVE_SCOPES
    if isinstance(scopes, str):
        scopes = scopes.split()
    info = dict(token)
    if "access_token" in info and "token" not in info:
        info["token"] = info["access_token"]
    if info.get("expiry"):
        info["expiry"] = _google_expiry(info["expiry"])
    return Credentials.from_authorized_user_info(info, scopes=scopes)


def credentials_from_token(token: GoogleToken):
    """Build google.oauth2 Credentials from a decrypted GoogleToken domain object."""
    return build_oauth_credentials(
        {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_uri": token.token_uri,
            "client_id": token.client_id,
            "client_secret": token.client_secret,
            "scopes": token.scopes,
            "expiry": token.expiry,
        }
    )


def _google_expiry(value: str) -> str:
    if value.endswith("Z"):
        return value.removesuffix("Z")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0).isoformat()
```

In `app/web/services.py`, replace the local `build_oauth_credentials` and `_google_expiry` definitions with an import so any remaining references keep working until Task 15 deletes the module:

```python
from app.google_auth import build_oauth_credentials  # noqa: F401
```

(Delete the old `build_oauth_credentials` and `_google_expiry` function bodies from `services.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_google_auth.py tests/test_web_services.py -v`
Expected: PASS (new google_auth tests pass; existing `test_build_oauth_credentials_maps_web_token_format` in test_web_services still passes via the re-export)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/google_auth.py app/web/services.py tests/test_google_auth.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "extract shared google_auth credentials helper"
```

---

## Task 8: WorkerSettings

**Files:**
- Create: `app/worker/__init__.py` (empty), `app/worker/config.py`
- Test: `tests/test_worker_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_worker_config.py`:

```python
from pathlib import Path

from app.worker.config import WorkerSettings


def test_defaults_when_env_is_empty():
    settings = WorkerSettings.from_env({})
    assert settings.repository_backend == "postgres"
    assert settings.poll_interval_seconds == 10
    assert settings.concurrency == 1
    assert settings.stale_job_timeout_minutes == 60
    assert settings.tmp_dir == Path("/app/tmp")


def test_reads_overrides_and_lowercases_backend():
    settings = WorkerSettings.from_env(
        {
            "WORKER_REPOSITORY_BACKEND": "Memory",
            "WORKER_POLL_INTERVAL_SECONDS": "5",
            "WORKER_CONCURRENCY": "3",
            "STALE_JOB_TIMEOUT_MINUTES": "15",
            "TMP_DIR": "/tmp/worker",
            "DEEPGRAM_MODEL": "nova-2",
        }
    )
    assert settings.repository_backend == "memory"
    assert settings.poll_interval_seconds == 5
    assert settings.concurrency == 3
    assert settings.stale_job_timeout_minutes == 15
    assert settings.tmp_dir == Path("/tmp/worker")
    assert settings.deepgram_model == "nova-2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_config.py -v`
Expected: FAIL with `No module named 'app.worker'`

- [ ] **Step 3: Write minimal implementation**

`app/worker/__init__.py`: empty file.

`app/worker/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import parse_bool


@dataclass(frozen=True)
class WorkerSettings:
    repository_backend: str
    poll_interval_seconds: int
    concurrency: int
    stale_job_timeout_minutes: int
    tmp_dir: Path
    deepgram_model: str
    deepgram_language: str
    deepgram_smart_format: bool
    deepgram_punctuate: bool
    deepgram_diarize: bool
    deepgram_utterances: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WorkerSettings":
        values = env if env is not None else os.environ
        backend = values.get("WORKER_REPOSITORY_BACKEND", "postgres").strip().lower()
        return cls(
            repository_backend=backend or "postgres",
            poll_interval_seconds=_positive_int(values, "WORKER_POLL_INTERVAL_SECONDS", 10),
            concurrency=_positive_int(values, "WORKER_CONCURRENCY", 1),
            stale_job_timeout_minutes=_positive_int(values, "STALE_JOB_TIMEOUT_MINUTES", 60),
            tmp_dir=Path(values.get("TMP_DIR", "/app/tmp")),
            deepgram_model=values.get("DEEPGRAM_MODEL", "nova-3").strip() or "nova-3",
            deepgram_language=values.get("DEEPGRAM_LANGUAGE", "pt-BR").strip() or "pt-BR",
            deepgram_smart_format=parse_bool(values.get("DEEPGRAM_SMART_FORMAT", "true")),
            deepgram_punctuate=parse_bool(values.get("DEEPGRAM_PUNCTUATE", "true")),
            deepgram_diarize=parse_bool(values.get("DEEPGRAM_DIARIZE", "true")),
            deepgram_utterances=parse_bool(values.get("DEEPGRAM_UTTERANCES", "true")),
        )


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return number
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/worker/__init__.py app/worker/config.py tests/test_worker_config.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add worker settings from env"
```

---

## Task 9: WorkerContainer

**Files:**
- Create: `app/worker/container.py`
- Test: `tests/test_worker_container.py`

- [ ] **Step 1: Write the failing test**

`tests/test_worker_container.py`:

```python
from pathlib import Path

import pytest

from app.deepgram_client import DeepgramClient
from app.repositories import RepositoryBackendError
from app.repositories.memory import InMemoryJobRepository
from app.worker.config import WorkerSettings
from app.worker.container import build_container


def _settings(backend, tmp_path):
    return WorkerSettings(
        repository_backend=backend, poll_interval_seconds=10, concurrency=1,
        stale_job_timeout_minutes=60, tmp_dir=Path(tmp_path),
        deepgram_model="nova-3", deepgram_language="pt-BR",
        deepgram_smart_format=True, deepgram_punctuate=True,
        deepgram_diarize=True, deepgram_utterances=True,
    )


def test_build_container_with_memory_backend(tmp_path):
    container = build_container(_settings("memory", tmp_path))
    assert isinstance(container.repositories.jobs, InMemoryJobRepository)

    deepgram = container.build_deepgram_client("user-key")
    assert isinstance(deepgram, DeepgramClient)
    assert deepgram.api_key == "user-key"
    assert deepgram.model == "nova-3"


def test_build_container_with_postgres_backend_fails_clearly(tmp_path):
    with pytest.raises(RepositoryBackendError):
        build_container(_settings("postgres", tmp_path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_container.py -v`
Expected: FAIL with `No module named 'app.worker.container'`

- [ ] **Step 3: Write minimal implementation**

`app/worker/container.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.ports import Repositories
from app.deepgram_client import DeepgramClient
from app.drive_client import DriveClient
from app.google_auth import credentials_from_token
from app.repositories import build_repositories
from app.worker.config import WorkerSettings


@dataclass
class WorkerContainer:
    settings: WorkerSettings
    repositories: Repositories
    build_drive_client: Callable
    build_deepgram_client: Callable
    credentials_from_token: Callable


def build_container(settings: WorkerSettings | None = None) -> WorkerContainer:
    worker_settings = settings or WorkerSettings.from_env()
    repositories = build_repositories(worker_settings.repository_backend)

    def build_drive_client(credentials, source_folder_id, destination_folder_id):
        return DriveClient.from_credentials(
            credentials, source_folder_id, destination_folder_id
        )

    def build_deepgram_client(api_key: str):
        return DeepgramClient.from_api_key(
            api_key,
            model=worker_settings.deepgram_model,
            language=worker_settings.deepgram_language,
            smart_format=worker_settings.deepgram_smart_format,
            punctuate=worker_settings.deepgram_punctuate,
            diarize=worker_settings.deepgram_diarize,
            utterances=worker_settings.deepgram_utterances,
        )

    return WorkerContainer(
        settings=worker_settings,
        repositories=repositories,
        build_drive_client=build_drive_client,
        build_deepgram_client=build_deepgram_client,
        credentials_from_token=credentials_from_token,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_container.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/worker/container.py tests/test_worker_container.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add worker container wiring"
```

---

## Task 10: Test support fakes + JobProcessor

**Files:**
- Create: `tests/support.py`, `app/worker/processor.py`
- Test: `tests/test_worker_processor.py`

- [ ] **Step 1: Write the shared support module and the failing test**

`tests/support.py`:

```python
from __future__ import annotations

from pathlib import Path

from app.processor import DriveFile
from app.repositories.memory import build_memory_repositories
from app.worker.config import WorkerSettings
from app.worker.container import WorkerContainer


def drive_file(file_id: str, name: str = "meeting.mp4") -> DriveFile:
    return DriveFile(
        id=file_id, name=name, mime_type="video/mp4", size=10,
        created_time="2026-06-03T10:00:00Z", modified_time="2026-06-03T10:00:00Z",
    )


class FakeDriveClient:
    def __init__(self, files=None, upload_result="drive-txt-1",
                 fail_download=False, fail_upload=False):
        self.files = list(files or [])
        self.upload_result = upload_result
        self.fail_download = fail_download
        self.fail_upload = fail_upload
        self.downloaded: list[str] = []
        self.uploaded: list[str] = []

    def list_video_files(self):
        return list(self.files)

    def download_by_id(self, file_id, destination):
        if self.fail_download:
            raise RuntimeError("download failed")
        self.downloaded.append(file_id)
        Path(destination).write_bytes(b"mp4 bytes")

    def upload_text_file(self, source_path, filename):
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.uploaded.append(filename)
        return self.upload_result


class FakeDeepgramClient:
    def __init__(self, response=None, fail=False):
        self.response = response or {
            "results": {"utterances": [
                {"start": 1.0, "speaker": 0, "transcript": "Ola mundo."}
            ]}
        }
        self.fail = fail
        self.api_key = None

    def transcribe(self, video_path, api_key=None):
        if self.fail:
            raise RuntimeError("deepgram failed")
        return self.response


def make_worker_settings(tmp_dir, **overrides) -> WorkerSettings:
    base = dict(
        repository_backend="memory", poll_interval_seconds=1, concurrency=1,
        stale_job_timeout_minutes=60, tmp_dir=Path(tmp_dir),
        deepgram_model="nova-3", deepgram_language="pt-BR",
        deepgram_smart_format=True, deepgram_punctuate=True,
        deepgram_diarize=True, deepgram_utterances=True,
    )
    base.update(overrides)
    return WorkerSettings(**base)


def make_worker_container(tmp_dir, repositories=None, drive=None, deepgram=None):
    repositories = repositories if repositories is not None else build_memory_repositories()
    drive = drive if drive is not None else FakeDriveClient()
    deepgram = deepgram if deepgram is not None else FakeDeepgramClient()

    def build_deepgram(api_key):
        deepgram.api_key = api_key
        return deepgram

    return WorkerContainer(
        settings=make_worker_settings(tmp_dir),
        repositories=repositories,
        build_drive_client=lambda credentials, src, dst: drive,
        build_deepgram_client=build_deepgram,
        credentials_from_token=lambda token: object(),
    )
```

`tests/test_worker_processor.py`:

```python
from datetime import datetime, timezone

from app.core.models import GoogleToken, JobStatus, Settings
from app.worker.processor import JobProcessor
from tests.support import FakeDeepgramClient, FakeDriveClient, make_worker_container


def _now():
    return datetime.now(timezone.utc)


def _seed(repos, *, save_copy=False, deepgram_key="user-dg-key"):
    repos.settings.set(Settings(
        user_id=7, source_drive_folder_id="src", destination_drive_folder_id="dst",
        poll_interval_seconds=300, save_copy_to_drive=save_copy, deepgram_api_key=deepgram_key,
    ))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def _claim_one(repos, source_file_id="src-1", name="meeting.mp4"):
    repos.jobs.create_job(7, source_file_id, name, _now())
    return repos.jobs.claim_next_pending_job("w1", _now())


def test_process_completes_and_persists_transcript(tmp_path):
    drive = FakeDriveClient()
    deepgram = FakeDeepgramClient()
    container = make_worker_container(tmp_path, drive=drive, deepgram=deepgram)
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert "Ola mundo." in transcript.text
    assert transcript.json_payload == deepgram.response
    assert deepgram.api_key == "user-dg-key"   # per-user key used
    assert drive.downloaded == ["src-1"]


def test_process_uploads_to_drive_when_enabled(tmp_path):
    drive = FakeDriveClient(upload_result="drive-txt-9")
    container = make_worker_container(tmp_path, drive=drive)
    _seed(container.repositories, save_copy=True)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    assert drive.uploaded and drive.uploaded[0].endswith("_Transcricao.txt")
    done = container.repositories.jobs.get_job(job.id)
    assert done.transcript_drive_file_id == "drive-txt-9"
    assert container.repositories.transcripts.get_by_job(job.id).drive_file_id == "drive-txt-9"


def test_process_skips_drive_upload_when_disabled(tmp_path):
    drive = FakeDriveClient()
    container = make_worker_container(tmp_path, drive=drive)
    _seed(container.repositories, save_copy=False)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    assert drive.uploaded == []
    assert container.repositories.jobs.get_job(job.id).transcript_drive_file_id is None


def test_process_fails_when_no_per_user_deepgram_key(tmp_path):
    container = make_worker_container(tmp_path)
    _seed(container.repositories, deepgram_key=None)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "Deepgram" in done.error_message


def test_process_marks_failed_on_transcription_error(tmp_path):
    container = make_worker_container(
        tmp_path, deepgram=FakeDeepgramClient(fail=True)
    )
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "deepgram failed" in done.error_message
    assert container.repositories.transcripts.get_by_job(job.id) is None


def test_process_cleans_only_its_own_job_dir(tmp_path):
    container = make_worker_container(tmp_path)
    _seed(container.repositories)
    job = _claim_one(container.repositories)
    # A sibling job's workspace must survive.
    sibling = tmp_path / "jobs" / "999"
    sibling.mkdir(parents=True)
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")

    JobProcessor(container).process(job)

    assert not (tmp_path / "jobs" / str(job.id)).exists()
    assert (sibling / "keep.txt").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_processor.py -v`
Expected: FAIL with `No module named 'app.worker.processor'`

- [ ] **Step 3: Write minimal implementation**

`app/worker/processor.py`:

```python
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.core.models import Job
from app.processor import format_transcript, sanitize_filename
from app.worker.container import WorkerContainer

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobProcessor:
    def __init__(self, container: WorkerContainer, now=_utc_now) -> None:
        self.container = container
        self._now = now

    def process(self, job: Job) -> None:
        repos = self.container.repositories
        job_dir = Path(self.container.settings.tmp_dir) / "jobs" / str(job.id)
        try:
            settings = repos.settings.get(job.user_id)
            if settings is None:
                raise RuntimeError("User settings are required before transcription")
            token = repos.google_tokens.get(job.user_id)
            if token is None:
                raise RuntimeError("Google token is required before transcription")
            if not settings.deepgram_api_key:
                raise RuntimeError("A per-user Deepgram API key is required")
            if not job.source_file_id:
                raise RuntimeError("Job has no source_file_id to download")

            credentials = self.container.credentials_from_token(token)
            drive = self.container.build_drive_client(
                credentials,
                settings.source_drive_folder_id,
                settings.destination_drive_folder_id,
            )
            deepgram = self.container.build_deepgram_client(settings.deepgram_api_key)

            job_dir.mkdir(parents=True, exist_ok=True)
            safe_base = sanitize_filename(job.source_file_name or job.source_file_id)
            video_path = job_dir / f"{safe_base}.mp4"
            drive.download_by_id(job.source_file_id, video_path)

            deepgram_response = deepgram.transcribe(video_path)
            transcript_text = format_transcript(
                deepgram_response, job.source_file_name or "", job.source_file_id
            )

            transcript_drive_file_id = None
            if settings.save_copy_to_drive and settings.destination_drive_folder_id:
                transcript_filename = f"{safe_base}_Transcricao.txt"
                transcript_path = job_dir / transcript_filename
                transcript_path.write_text(transcript_text, encoding="utf-8")
                transcript_drive_file_id = drive.upload_text_file(
                    transcript_path, transcript_filename
                )

            repos.transcripts.create(
                job_id=job.id, user_id=job.user_id, text=transcript_text,
                json_payload=deepgram_response, drive_file_id=transcript_drive_file_id,
                now=self._now(),
            )
            repos.jobs.mark_completed(
                job.id, self._now(), transcript_drive_file_id=transcript_drive_file_id
            )
            LOGGER.info("Job completed job_id=%s", job.id)
        except Exception as exc:  # noqa: BLE001 - a job must always reach a terminal state.
            LOGGER.exception("Job failed job_id=%s", job.id)
            repos.jobs.mark_failed(job.id, str(exc), self._now())
        finally:
            _cleanup_job_dir(job_dir)


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_processor.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add tests/support.py app/worker/processor.py tests/test_worker_processor.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add job processor with per-job cleanup"
```

---

## Task 11: Worker loop

**Files:**
- Create: `app/worker/loop.py`
- Test: `tests/test_worker_loop.py`

- [ ] **Step 1: Write the failing test**

`tests/test_worker_loop.py`:

```python
import threading
from datetime import datetime, timezone

from app.core.models import JobStatus
from app.worker.loop import run_worker_loop
from tests.support import make_worker_container


def _now():
    return datetime.now(timezone.utc)


class RecordingProcessor:
    def __init__(self, repositories):
        self.repositories = repositories
        self.processed = []

    def process(self, job):
        self.processed.append(job.id)
        self.repositories.jobs.mark_completed(job.id, _now())


def test_loop_claims_and_processes_then_stops_when_idle(tmp_path):
    container = make_worker_container(tmp_path)
    job = container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    processor = RecordingProcessor(container.repositories)
    stop_event = threading.Event()

    # When the queue is empty the loop "sleeps"; use that hook to stop the loop.
    def fake_sleep(_seconds):
        stop_event.set()

    run_worker_loop(container, stop_event, "w1", processor=processor, sleep=fake_sleep)

    assert processor.processed == [job.id]
    assert container.repositories.jobs.get_job(job.id).status == JobStatus.COMPLETED.value


def test_loop_exits_immediately_when_already_stopped(tmp_path):
    container = make_worker_container(tmp_path)
    container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    processor = RecordingProcessor(container.repositories)
    stop_event = threading.Event()
    stop_event.set()

    run_worker_loop(container, stop_event, "w1", processor=processor, sleep=lambda s: None)

    assert processor.processed == []  # never claimed because stop was set first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_loop.py -v`
Expected: FAIL with `No module named 'app.worker.loop'`

- [ ] **Step 3: Write minimal implementation**

`app/worker/loop.py`:

```python
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.worker.container import WorkerContainer
from app.worker.processor import JobProcessor

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_worker_loop(
    container: WorkerContainer,
    stop_event: threading.Event,
    worker_id: str,
    processor: JobProcessor | None = None,
    now=_utc_now,
    sleep=None,
) -> None:
    proc = processor or JobProcessor(container)
    # Default sleep returns as soon as stop is set, enabling fast shutdown.
    sleeper = sleep or stop_event.wait
    while not stop_event.is_set():
        job = container.repositories.jobs.claim_next_pending_job(worker_id, now())
        if job is None:
            sleeper(container.settings.poll_interval_seconds)
            continue
        LOGGER.info("Claimed job_id=%s worker=%s", job.id, worker_id)
        proc.process(job)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_loop.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/worker/loop.py tests/test_worker_loop.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add worker poll loop"
```

---

## Task 12: Worker main + stale recovery

**Files:**
- Create: `app/worker/main.py`
- Test: `tests/test_worker_main.py`

- [ ] **Step 1: Write the failing test**

`tests/test_worker_main.py`:

```python
import threading
from datetime import datetime, timedelta, timezone

from app.core.models import JobStatus
from app.worker.main import recover_stale_jobs, run
from tests.support import make_worker_container


def _now():
    return datetime.now(timezone.utc)


def test_recover_stale_jobs_marks_old_processing_failed(tmp_path):
    container = make_worker_container(tmp_path)
    job = container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    container.repositories.jobs.claim_next_pending_job("w", _now())
    container.repositories.jobs._jobs[job.id].started_at = _now() - timedelta(hours=5)

    count = recover_stale_jobs(container, _now())

    assert count == 1
    assert container.repositories.jobs.get_job(job.id).status == JobStatus.FAILED.value


def test_run_recovers_stale_then_exits_when_stop_is_preset(tmp_path):
    container = make_worker_container(tmp_path)
    job = container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    container.repositories.jobs.claim_next_pending_job("w", _now())
    container.repositories.jobs._jobs[job.id].started_at = _now() - timedelta(hours=5)
    stop_event = threading.Event()
    stop_event.set()  # threads exit immediately; run() must not hang

    run(container, stop_event)

    assert container.repositories.jobs.get_job(job.id).status == JobStatus.FAILED.value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_main.py -v`
Expected: FAIL with `No module named 'app.worker.main'`

- [ ] **Step 3: Write minimal implementation**

`app/worker/main.py`:

```python
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

from app.logger import setup_logging
from app.worker.container import WorkerContainer, build_container
from app.worker.loop import run_worker_loop

LOGGER = logging.getLogger(__name__)


def recover_stale_jobs(container: WorkerContainer, now: datetime) -> int:
    stale_before = now - timedelta(minutes=container.settings.stale_job_timeout_minutes)
    reset = container.repositories.jobs.reset_stale_processing_jobs(stale_before, now)
    if reset:
        LOGGER.warning("Recovered %s stale processing job(s) to failed", len(reset))
    return len(reset)


def run(container: WorkerContainer, stop_event: threading.Event) -> None:
    recover_stale_jobs(container, datetime.now(timezone.utc))
    threads: list[threading.Thread] = []
    for i in range(container.settings.concurrency):
        worker_id = f"worker-{i + 1}"
        thread = threading.Thread(
            target=run_worker_loop,
            args=(container, stop_event, worker_id),
            name=worker_id,
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


def main() -> int:
    setup_logging()
    container = build_container()
    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        LOGGER.info("Received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    LOGGER.info(
        "Worker starting backend=%s concurrency=%s",
        container.settings.repository_backend,
        container.settings.concurrency,
    )
    run(container, stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_main.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/worker/main.py tests/test_worker_main.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add worker entrypoint and stale recovery"
```

---

## Task 13: Job creation service

**Files:**
- Create: `app/services/__init__.py` (empty), `app/services/job_service.py`
- Test: `tests/test_job_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_job_service.py`:

```python
from datetime import datetime, timezone

from app.core.models import GoogleToken, JobStatus, Settings
from app.repositories.memory import build_memory_repositories
from app.services.job_service import create_next_pending_job
from tests.support import FakeDriveClient, drive_file


def _now():
    return datetime.now(timezone.utc)


def _build(files, *, with_settings=True, with_token=True):
    repos = build_memory_repositories()
    if with_settings:
        repos.settings.set(Settings(7, "src", "dst", 300, False, "dg"))
    if with_token:
        repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))
    drive = FakeDriveClient(files=files)
    return repos, drive


def _call(repos, drive):
    return create_next_pending_job(
        repos,
        build_drive_client=lambda credentials, src, dst: drive,
        credentials_from_token=lambda token: object(),
        user_id=7,
    )


def test_reports_no_settings():
    repos = build_memory_repositories()
    result = _call(repos, FakeDriveClient())
    assert result.status == "no_settings"


def test_reports_not_connected():
    repos = build_memory_repositories()
    repos.settings.set(Settings(7, "src", "dst", 300, False, "dg"))
    result = _call(repos, FakeDriveClient())
    assert result.status == "not_connected"


def test_creates_pending_job_for_first_new_video():
    repos, drive = _build([drive_file("file-1", "a.mp4"), drive_file("file-2", "b.mp4")])
    result = _call(repos, drive)
    assert result.status == "created"
    assert result.job.status == JobStatus.PENDING.value
    assert result.job.source_file_id == "file-1"
    assert result.job.source_file_name == "a.mp4"


def test_skips_already_completed_video():
    repos, drive = _build([drive_file("file-1", "a.mp4"), drive_file("file-2", "b.mp4")])
    done = repos.jobs.create_job(7, "file-1", "a.mp4", _now())
    repos.jobs.mark_completed(done.id, _now())
    result = _call(repos, drive)
    assert result.status == "created"
    assert result.job.source_file_id == "file-2"


def test_skips_video_with_active_job():
    repos, drive = _build([drive_file("file-1", "a.mp4"), drive_file("file-2", "b.mp4")])
    repos.jobs.create_job(7, "file-1", "a.mp4", _now())  # pending
    result = _call(repos, drive)
    assert result.job.source_file_id == "file-2"


def test_reports_no_new_videos_when_all_taken():
    repos, drive = _build([drive_file("file-1", "a.mp4")])
    repos.jobs.create_job(7, "file-1", "a.mp4", _now())  # pending blocks it
    result = _call(repos, drive)
    assert result.status == "no_new_videos"
    assert result.job is None


def test_does_not_duplicate_when_run_twice():
    repos, drive = _build([drive_file("file-1", "a.mp4")])
    first = _call(repos, drive)
    second = _call(repos, drive)
    assert first.status == "created"
    assert second.status == "no_new_videos"
    assert len(repos.jobs.list_jobs_for_user(7)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_job_service.py -v`
Expected: FAIL with `No module named 'app.services'`

- [ ] **Step 3: Write minimal implementation**

`app/services/__init__.py`: empty file.

`app/services/job_service.py`:

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.core.models import Job
from app.core.ports import Repositories

LOGGER = logging.getLogger(__name__)

# A video is unavailable for a new job if it is already queued, running, or done.
BLOCKING_STATUSES = ("pending", "processing", "completed")


@dataclass(frozen=True)
class JobCreationResult:
    status: str  # created | no_settings | not_connected | no_new_videos
    job: Job | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_next_pending_job(
    repositories: Repositories,
    build_drive_client: Callable,
    credentials_from_token: Callable,
    user_id: int,
    now: Callable[[], datetime] = _utc_now,
) -> JobCreationResult:
    settings = repositories.settings.get(user_id)
    if settings is None or not settings.source_drive_folder_id:
        return JobCreationResult("no_settings")

    token = repositories.google_tokens.get(user_id)
    if token is None:
        return JobCreationResult("not_connected")

    credentials = credentials_from_token(token)
    drive = build_drive_client(
        credentials, settings.source_drive_folder_id, settings.destination_drive_folder_id
    )

    for file in drive.list_video_files():
        existing = repositories.jobs.find_existing_job(user_id, file.id, BLOCKING_STATUSES)
        if existing is not None:
            continue
        job = repositories.jobs.create_job(
            user_id=user_id, source_file_id=file.id,
            source_file_name=file.name, now=now(),
        )
        LOGGER.info(
            "Created pending job job_id=%s user_id=%s file=%s", job.id, user_id, file.id
        )
        return JobCreationResult("created", job)

    return JobCreationResult("no_new_videos")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_job_service.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/services/__init__.py app/services/job_service.py tests/test_job_service.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add job creation service with dedup"
```

---

## Task 14: Download service

**Files:**
- Create: `app/services/download_service.py`
- Test: `tests/test_download_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_download_service.py`:

```python
from datetime import datetime, timezone

import pytest

from app.repositories.memory import build_memory_repositories
from app.services.download_service import DownloadError, get_downloadable_transcript


def _now():
    return datetime.now(timezone.utc)


def _completed_job_with_transcript(repos, user_id=7, name="Weekly Sync.mp4"):
    job = repos.jobs.create_job(user_id, "src-1", name, _now())
    repos.jobs.claim_next_pending_job("w", _now())
    repos.transcripts.create(job.id, user_id, "transcript text", {"k": "v"}, None, _now())
    repos.jobs.mark_completed(job.id, _now())
    return job


def test_owner_downloads_sanitized_attachment():
    repos = build_memory_repositories()
    job = _completed_job_with_transcript(repos)
    result = get_downloadable_transcript(repos, job.id, requester_user_id=7)
    assert result.text == "transcript text"
    assert result.filename == "Weekly_Sync_Transcricao.txt"


def test_admin_downloads_other_users_job():
    repos = build_memory_repositories()
    job = _completed_job_with_transcript(repos, user_id=7)
    result = get_downloadable_transcript(repos, job.id, requester_user_id=99, is_admin=True)
    assert result.text == "transcript text"


def test_stranger_is_denied_as_not_found():
    repos = build_memory_repositories()
    job = _completed_job_with_transcript(repos, user_id=7)
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, job.id, requester_user_id=99)
    assert exc.value.code == "not_found"


def test_missing_job_is_not_found():
    repos = build_memory_repositories()
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, 12345, requester_user_id=7)
    assert exc.value.code == "not_found"


def test_not_completed_job_is_rejected():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())  # pending
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, job.id, requester_user_id=7)
    assert exc.value.code == "not_completed"


def test_completed_without_transcript_is_rejected():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.mark_completed(job.id, _now())  # completed but no transcript row
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, job.id, requester_user_id=7)
    assert exc.value.code == "no_transcript"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_download_service.py -v`
Expected: FAIL with `No module named 'app.services.download_service'`

- [ ] **Step 3: Write minimal implementation**

`app/services/download_service.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from app.core.models import JobStatus
from app.core.ports import Repositories
from app.processor import sanitize_filename


class DownloadError(Exception):
    """Raised when a transcript cannot be served. `code` is a stable reason string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # not_found | not_completed | no_transcript


@dataclass(frozen=True)
class DownloadableTranscript:
    filename: str
    text: str


def get_downloadable_transcript(
    repositories: Repositories,
    job_id: int,
    requester_user_id: int,
    is_admin: bool = False,
) -> DownloadableTranscript:
    job = repositories.jobs.get_job(job_id)
    if job is None or (job.user_id != requester_user_id and not is_admin):
        # Do not leak existence of other users' jobs.
        raise DownloadError("not_found", "Job not found")
    if job.status != JobStatus.COMPLETED.value:
        raise DownloadError("not_completed", "Job is not completed yet")
    transcript = repositories.transcripts.get_by_job(job_id)
    if transcript is None:
        raise DownloadError("no_transcript", "Transcript is not available")
    base = sanitize_filename(job.source_file_name or f"job_{job_id}")
    return DownloadableTranscript(filename=f"{base}_Transcricao.txt", text=transcript.text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_download_service.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/services/download_service.py tests/test_download_service.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "add transcript download service"
```

---

## Task 15: Web — run-once + download routes via ports

**Files:**
- Modify: `app/web/main.py`
- Delete: `app/web/services.py`, `tests/test_web_services.py`
- Modify: `tests/test_web_routes.py` (rewrite jobs-related tests; keep auth/oauth tests)
- Create: `tests/test_jobs_download_route.py`

This task repoints `POST /jobs/run-once` to create a pending job via `job_service` and adds `GET /jobs/{job_id}/download`. It introduces `create_app(settings, repositories=None)` so tests inject in-memory repositories. The jobs/dashboard listing pages move to ports in Task 16.

- [ ] **Step 1: Write the failing tests**

`tests/test_jobs_download_route.py`:

```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.models import GoogleToken, Settings
from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.main import create_app
from tests.support import FakeDriveClient, drive_file


def _now():
    return datetime.now(timezone.utc)


def _web_settings(tmp_path) -> WebSettings:
    return WebSettings.from_env(
        {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "secret",
            "APP_SECRET_KEY": "a-long-secret-for-tests",
            "SESSION_COOKIE_SECURE": "false",
            "GOOGLE_WEB_CLIENT_ID": "client-id",
            "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
            "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
            "DATABASE_URL": str(tmp_path / "app.db"),
            "DEEPGRAM_API_KEY": "dg-key",
            "TMP_DIR": str(tmp_path / "tmp"),
        }
    )


def _login(client):
    assert client.post(
        "/login", data={"username": "admin", "password": "secret"}, follow_redirects=False
    ).status_code in {302, 303}


def _seed_user1(repos):
    # Admin login creates user id=1 in SQLite; seed the PG-side ports for that id.
    repos.settings.set(Settings(1, "src", "dst", 300, False, "user-dg-key"))
    repos.google_tokens.set(1, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def test_run_once_creates_pending_job_without_processing(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    drive = FakeDriveClient(files=[drive_file("file-1", "meet.mp4")])
    app = create_app(_web_settings(tmp_path), repositories=repos)
    # Patch the drive factory used by the route so no real Google call happens.
    app.state.build_drive_client = lambda credentials, src, dst: drive
    app.state.credentials_from_token = lambda token: object()

    with TestClient(app) as client:
        _login(client)
        response = client.post("/jobs/run-once", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"

    jobs = repos.jobs.list_jobs_for_user(1)
    assert len(jobs) == 1
    assert jobs[0].status == "pending"
    assert jobs[0].source_file_id == "file-1"


def test_run_once_without_settings_redirects_with_message(tmp_path):
    repos = build_memory_repositories()  # no settings seeded
    app = create_app(_web_settings(tmp_path), repositories=repos)
    with TestClient(app) as client:
        _login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        page = client.get("/jobs")
    assert "Configure source and destination folders" in page.text
    assert repos.jobs.list_jobs_for_user(1) == []


def test_download_returns_owner_transcript(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    job = repos.jobs.create_job(1, "file-1", "Weekly Sync.mp4", _now())
    repos.transcripts.create(job.id, 1, "the transcript body", {"k": "v"}, None, _now())
    repos.jobs.mark_completed(job.id, _now())
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)
        response = client.get(f"/jobs/{job.id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Weekly_Sync_Transcricao.txt" in response.headers["content-disposition"]
    assert response.text == "the transcript body"


def test_download_of_other_users_job_is_404(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    other = repos.jobs.create_job(2, "file-x", "other.mp4", _now())
    repos.transcripts.create(other.id, 2, "secret", None, None, _now())
    repos.jobs.mark_completed(other.id, _now())
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)  # logs in as user id=1
        response = client.get(f"/jobs/{other.id}/download")

    assert response.status_code == 404


def test_download_of_pending_job_is_409(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    job = repos.jobs.create_job(1, "file-1", "meet.mp4", _now())  # pending
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)
        response = client.get(f"/jobs/{job.id}/download")

    assert response.status_code == 409
```

Also rewrite `tests/test_web_routes.py`: **delete** `test_run_once_responds_fast_with_pending_job_and_background_task`, `test_run_once_blocks_when_a_job_is_already_running`, `test_run_once_without_settings_redirects_with_message`, `test_jobs_page_shows_all_job_fields_and_refresh_guidance`, and `test_jobs_page_shows_error_message_for_failed_job` (run-once + jobs-listing behavior is now covered by `tests/test_jobs_download_route.py` and Task 16). Keep `test_health_*`, `test_create_app_initializes_database_on_startup`, `test_protected_dashboard_redirects_to_login`, `test_login_sets_http_only_session_cookie`, `test_connect_google_*`, and `test_oauth_callback_*`. Change `test_authenticated_settings_and_jobs_render` to pass injected memory repos:

```python
def test_authenticated_settings_and_jobs_render(tmp_path):
    from app.repositories.memory import build_memory_repositories

    app = create_app(_settings(tmp_path), repositories=build_memory_repositories())
    with TestClient(app) as client:
        _login(client)
        assert client.get("/settings").status_code == 200
        assert client.get("/jobs").status_code == 200
```

Delete `app/web/services.py` and `tests/test_web_services.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_jobs_download_route.py -v`
Expected: FAIL (route `/jobs/{job_id}/download` missing; `create_app` has no `repositories` kwarg)

- [ ] **Step 3: Write minimal implementation**

Edit `app/web/main.py`:

1. Update imports at the top — remove `BackgroundTasks`, add the repositories factory and `PlainTextResponse`:

```python
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
```

and near the other `app.` imports:

```python
from app.drive_client import DriveClient
from app.google_auth import credentials_from_token
from app.repositories import RepositoryBackendError, build_repositories
```

2. Change the signature and add repository wiring inside `create_app`:

```python
def create_app(settings: WebSettings | None = None, repositories=None) -> FastAPI:
```

After `app.state.token_store = ...` add:

```python
    app.state.repositories = repositories
    app.state.build_drive_client = (
        lambda credentials, src, dst: DriveClient.from_credentials(credentials, src, dst)
    )
    app.state.credentials_from_token = credentials_from_token
```

3. Add a repositories resolver helper inside `create_app` (above the routes):

```python
    def _resolve_repositories():
        if app.state.repositories is not None:
            return app.state.repositories, None
        import os

        try:
            return build_repositories(os.environ.get("WORKER_REPOSITORY_BACKEND")), None
        except RepositoryBackendError as exc:
            return None, str(exc)
```

4. Replace the whole `run_once` route with:

```python
    @app.post("/jobs/run-once")
    def run_once(request: Request, user=Depends(require_user)):
        from app.services.job_service import create_next_pending_job

        repositories, error = _resolve_repositories()
        if repositories is None:
            _set_flash(request, error)
            return RedirectResponse("/jobs", status_code=303)

        result = create_next_pending_job(
            repositories,
            build_drive_client=app.state.build_drive_client,
            credentials_from_token=app.state.credentials_from_token,
            user_id=user["id"],
        )
        messages = {
            "no_settings": "Configure source and destination folders in Settings first.",
            "not_connected": "Connect Google before running a transcription.",
            "no_new_videos": "No new videos to transcribe.",
            "created": "Job created. The worker will process it shortly.",
        }
        _set_flash(request, messages.get(result.status, "Run-once finished."))
        return RedirectResponse("/jobs", status_code=303)
```

5. Add the download route (place it after `run_once`):

```python
    @app.get("/jobs/{job_id}/download")
    def download_transcript(job_id: int, request: Request, user=Depends(require_user)):
        from app.services.download_service import DownloadError, get_downloadable_transcript

        repositories, error = _resolve_repositories()
        if repositories is None:
            raise HTTPException(status_code=503, detail=error)
        is_admin = request.session.get("user_email") == web_settings.admin_username
        try:
            result = get_downloadable_transcript(repositories, job_id, user["id"], is_admin)
        except DownloadError as exc:
            status = {"not_found": 404, "not_completed": 409, "no_transcript": 404}.get(
                exc.code, 400
            )
            raise HTTPException(status_code=status, detail=str(exc))
        return PlainTextResponse(
            result.text,
            headers={
                "Content-Disposition": f'attachment; filename="{result.filename}"'
            },
        )
```

Delete `app/web/services.py` and `tests/test_web_services.py`:

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker rm app/web/services.py tests/test_web_services.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_jobs_download_route.py tests/test_web_routes.py -v`
Expected: PASS (new download/run-once route tests + retained auth/oauth tests)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/web/main.py tests/test_jobs_download_route.py tests/test_web_routes.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "wire run-once and download routes to postgres ports"
```

---

## Task 16: Web — jobs/dashboard listing via ports + UI buttons

**Files:**
- Modify: `app/web/main.py` (jobs + dashboard reads), `app/web/templates/jobs.html`, `app/web/templates/dashboard.html`
- Test: `tests/test_jobs_download_route.py` (add listing/UI assertions)

- [ ] **Step 1: Write the failing test (append to `tests/test_jobs_download_route.py`)**

```python
def test_jobs_page_lists_jobs_and_shows_download_and_drive_links(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    done = repos.jobs.create_job(1, "file-1", "meet.mp4", _now())
    repos.transcripts.create(done.id, 1, "body", None, None, _now())
    repos.jobs.mark_completed(done.id, _now(), transcript_drive_file_id="drive-xyz")
    repos.jobs.create_job(1, "file-2", "pending.mp4", _now())
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)
        page = client.get("/jobs")

    text = page.text
    assert "meet.mp4" in text
    assert "pending.mp4" in text
    assert f"/jobs/{done.id}/download" in text        # Download button for completed job
    assert "drive.google.com/file/d/drive-xyz" in text  # Drive link when present


def test_jobs_page_handles_backend_unavailable_gracefully(tmp_path):
    # No repositories injected -> default postgres backend is unavailable on this branch.
    app = create_app(_web_settings(tmp_path), repositories=None)
    with TestClient(app) as client:
        _login(client)
        page = client.get("/jobs")
    assert page.status_code == 200
    assert "not available" in page.text.lower() or "postgres-core" in page.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jobs_download_route.py -v`
Expected: FAIL (jobs page still reads SQLite rows; no Download/Drive links; no backend notice)

- [ ] **Step 3: Write minimal implementation**

In `app/web/main.py`, replace the `dashboard` recent-jobs read and the `jobs_page` route to use the ports:

```python
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, user=Depends(require_user)):
        settings_row = db.get_settings(web_settings.database_path, user["id"])
        token_row = db.get_google_token(web_settings.database_path, user["id"])
        repositories, _ = _resolve_repositories()
        jobs = repositories.jobs.list_jobs_for_user(user["id"])[:5] if repositories else []
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "user": user,
                "settings": settings_row,
                "google_connected": token_row is not None,
                "jobs": jobs,
            },
        )
```

```python
    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, user=Depends(require_user)):
        repositories, error = _resolve_repositories()
        jobs = repositories.jobs.list_jobs_for_user(user["id"]) if repositories else []
        return templates.TemplateResponse(
            request,
            "jobs.html",
            {
                "user": user,
                "jobs": jobs,
                "message": _pop_flash(request),
                "backend_error": error,
            },
        )
```

Rewrite `app/web/templates/jobs.html` (jobs are now `Job` dataclasses — attribute access; add Download + Drive columns and the backend notice):

```html
{% extends "base.html" %}
{% block content %}
<section class="card">
  <div class="row-between">
    <div><h1>Jobs</h1><p>Manual and processed transcription jobs.</p></div>
    <form method="post" action="/jobs/run-once"><button type="submit">Run once</button></form>
  </div>
  {% if message %}<div class="notice">{{ message }}</div>{% endif %}
  {% if backend_error %}<div class="notice">{{ backend_error }}</div>{% endif %}
  <p class="hint">After starting a job, refresh this page to see updates.</p>
  {% if jobs %}
  <div class="table-scroll">
  <table>
    <thead>
      <tr>
        <th>File</th>
        <th>Source ID</th>
        <th>Status</th>
        <th>Attempts</th>
        <th>Transcript</th>
        <th>Error</th>
        <th>Created</th>
        <th>Updated</th>
        <th>Processed</th>
      </tr>
    </thead>
    <tbody>
    {% for job in jobs %}
      <tr>
        <td>{{ job.source_file_name or '-' }}</td>
        <td>{{ job.source_file_id or '-' }}</td>
        <td>{{ job.status }}</td>
        <td>{{ job.attempts }}</td>
        <td>
          {% if job.status == 'completed' %}
            <a href="/jobs/{{ job.id }}/download">Download</a>
            {% if job.transcript_drive_file_id %}
              · <a href="https://drive.google.com/file/d/{{ job.transcript_drive_file_id }}/view" target="_blank" rel="noopener">Drive</a>
            {% endif %}
          {% else %}-{% endif %}
        </td>
        <td>{{ job.error_message or '' }}</td>
        <td>{{ job.created_at or '-' }}</td>
        <td>{{ job.updated_at or '-' }}</td>
        <td>{{ job.processed_at or '-' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p>No jobs yet.</p>{% endif %}
</section>
{% endblock %}
```

Update the recent-jobs loop in `app/web/templates/dashboard.html` to attribute access:

```html
    {% for job in jobs %}<li>{{ job.source_file_name or "Manual run" }} — {{ job.status }}</li>{% endfor %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_jobs_download_route.py -v`
Expected: PASS (all route tests incl. listing + UI)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add app/web/main.py app/web/templates/jobs.html app/web/templates/dashboard.html tests/test_jobs_download_route.py
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "list jobs from ports and add download and drive links"
```

---

## Task 17: Env, compose, and README

**Files:**
- Modify: `.env.example`, `docker-compose.yml`, `README.md`

- [ ] **Step 1: Update `.env.example`** — append a worker section:

```env

# --- PostgreSQL multiuser worker (python -m app.worker.main) ---
# Backend default is 'postgres' (production). 'memory' is for local development
# and tests ONLY and is FORBIDDEN in production (non-persistent).
WORKER_REPOSITORY_BACKEND=postgres
WORKER_POLL_INTERVAL_SECONDS=10
WORKER_CONCURRENCY=1
STALE_JOB_TIMEOUT_MINUTES=60
# PostgreSQL DSN consumed by the postgres adapter (delivered by feat/postgres-core).
DATABASE_URL=postgresql://user:password@db:5432/meet_transcription
```

- [ ] **Step 2: Update `docker-compose.yml`** — add the worker service under `services:` (keep existing `worker` and `web`):

```yaml
  transcription-worker:
    <<: *app
    command: ["python", "-m", "app.worker.main"]
    volumes:
      - ./data:/app/data
      - ./tmp:/app/tmp
      - ./secrets:/app/secrets:ro
    # NOTE: requires PostgreSQL. The `db` service, `depends_on: [db]`, and the
    # SQLAlchemy/psycopg dependencies are delivered by feat/postgres-core. This
    # branch intentionally does not add them.
```

- [ ] **Step 3: Update `README.md`** — add a "PostgreSQL Multiuser Worker" section:

```markdown
## PostgreSQL Multiuser Worker

`python -m app.worker.main` runs a standalone worker that processes transcription
jobs created by the web UI. The UI creates a `pending` job; the worker claims it
safely (`FOR UPDATE SKIP LOCKED`), transcribes with the user's own Deepgram key,
stores the transcript in PostgreSQL, and optionally uploads a copy to Drive. The
UI offers a TXT download for completed jobs.

### Backend selection

- `WORKER_REPOSITORY_BACKEND` defaults to `postgres` (production).
- `WORKER_REPOSITORY_BACKEND=memory` is for local development and tests ONLY and
  is **forbidden in production** — it is in-memory and non-persistent.
- Selecting `postgres` before the PostgreSQL adapter (feat/postgres-core) is
  integrated fails fast with a clear error.

### Worker configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORKER_REPOSITORY_BACKEND` | `postgres` | `postgres` (prod) or `memory` (dev/test only) |
| `WORKER_POLL_INTERVAL_SECONDS` | `10` | Idle poll interval |
| `WORKER_CONCURRENCY` | `1` | Parallel job workers (safe via SKIP LOCKED) |
| `STALE_JOB_TIMEOUT_MINUTES` | `60` | `processing` jobs older than this are failed at startup |
| `DATABASE_URL` | — | PostgreSQL DSN (used by the postgres adapter) |

### Integration status

This branch (`feat/postgres-worker`) delivers the worker, job/download services,
repository ports, and in-memory fakes. The real PostgreSQL repositories, schema,
`db` service, and `SQLAlchemy`/`psycopg` dependencies are delivered by
`feat/postgres-core`; per-user settings (incl. the encrypted Deepgram key and
`save_copy_to_drive`) and OAuth-to-PostgreSQL repointing by
`feat/auth-users-settings`.
```

- [ ] **Step 4: Validate compose**

Run: `cd /home/gabedsam01/Documentos/meet-transcription-worker && docker compose config >/dev/null && echo OK`
Expected: `OK` (valid compose; no schema errors)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add .env.example docker-compose.yml README.md
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "document and wire worker env, compose service, and readme"
```

---

## Task 18: Full validation

**Files:** none (validation only)

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/gabedsam01/Documentos/meet-transcription-worker && python -m pytest -v`
Expected: All tests PASS (new worker/service/repo/route tests + retained config/db/state/processor/token/drive/deepgram/web auth tests). No references to the deleted `app/web/services.py`.

- [ ] **Step 2: Byte-compile**

Run: `cd /home/gabedsam01/Documentos/meet-transcription-worker && python -m compileall app scripts`
Expected: Compiles with no errors.

- [ ] **Step 3: Validate compose**

Run: `cd /home/gabedsam01/Documentos/meet-transcription-worker && docker compose config >/dev/null && echo OK`
Expected: `OK`.

- [ ] **Step 4: Build image**

Run: `cd /home/gabedsam01/Documentos/meet-transcription-worker && docker compose build`
Expected: Build succeeds (single image; no new heavy dependencies added on this branch).

- [ ] **Step 5: Smoke-run the worker on the memory backend (optional but recommended)**

Run:
```bash
cd /home/gabedsam01/Documentos/meet-transcription-worker && \
WORKER_REPOSITORY_BACKEND=memory WORKER_POLL_INTERVAL_SECONDS=1 TMP_DIR=./tmp \
timeout 3 python -m app.worker.main; echo "exit=$?"
```
Expected: Worker logs "Worker starting backend=memory concurrency=1", idles (no jobs), and is terminated by `timeout` (`exit=124`). Confirms the entrypoint wires up and the loop runs.

- [ ] **Step 6: Confirm the legacy CLI still imports and parses**

Run: `cd /home/gabedsam01/Documentos/meet-transcription-worker && python -c "import app.main, app.processor, app.state; print('cli ok')"`
Expected: `cli ok` (the legacy `--once/--watch/--reprocess` worker is untouched).

- [ ] **Step 7: Final commit (if any validation fixups were needed)**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-worker add -A
git -C /home/gabedsam01/Documentos/meet-transcription-worker commit -m "finalize postgres worker validation" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- Req 1 (new worker package, `python -m app.worker.main`, loop, polling, concurrency, no double-processing) → Tasks 8–12.
- Req 2 (SKIP LOCKED claim semantics, short tx, process outside tx) → contract in Task 2 (`ports.py`), enforced atomic-claim fake in Task 3, claim/loop in Tasks 3/11.
- Req 3 (job creation service: settings, token, list, next-not-completed, dedup, called by run-once) → Tasks 13 + 15.
- Req 4 (processing: load user/token/key, drive client, download, deepgram, format text, store JSONB, transcripts row, optional drive copy, mark completed) → Task 10.
- Req 5 (`/app/tmp/jobs/<job_id>/`, cleanup only that dir) → Task 10 (`_cleanup_job_dir`, sibling-survives test).
- Req 6 (failed + error_message + traceback in logs + never stuck + stale timeout at startup) → Tasks 10 + 12.
- Req 7 (`GET /jobs/{job_id}/download`, ownership/admin, completed, transcript exists, text/plain attachment, sanitized filename) → Tasks 14 + 15.
- Req 8 (Download button + Drive link, no layout break) → Task 16.
- Req 9 (DeepgramClient per-call/per-instance key, no global dependency in new flow) → Task 5.
- Req 10 (DriveClient from user credentials; don't break old) → Task 6 (`download_by_id`; `from_credentials` already exists).
- Req 11 (CLI untouched) → verified Task 18 Step 6; `app/main.py`/`state.py`/`processor.py` not modified.
- Req 12 (all listed tests) → Tasks 3, 5, 10, 13, 14, 15, 16.
- Req 13 (pytest, compileall, docker compose config, build) → Task 18.
- Req 14 (no Whisper/Celery/Redis/advanced multi-worker/advanced UI/GH Actions) → none added.
- Req 15 (deliverables summary) → produced at handoff.
- Mandatory adjustment (default postgres; memory forbidden in prod; postgres-not-integrated fails clearly) → Tasks 4 (factory), 8 (default), 17 (docs).

**Placeholder scan:** No TBD/TODO; every code step contains complete code.

**Type consistency:** `Repositories` bundle fields (`jobs`/`transcripts`/`settings`/`google_tokens`) consistent across ports, memory adapter, factory, container, services, routes. `JobCreationResult.status` strings (`created`/`no_settings`/`not_connected`/`no_new_videos`) consistent between service and route messages. `DownloadError.code` values (`not_found`/`not_completed`/`no_transcript`) consistent between service, tests, and route status mapping. `WorkerContainer` fields consistent between `container.py`, `tests/support.py`, processor, and loop. `claim_next_pending_job(worker_id, now)`, `create_job(user_id, source_file_id, source_file_name, now)`, `mark_completed(job_id, now, transcript_drive_file_id=None)` signatures consistent across adapter, ports, and callers.

**Note on the in-memory adapter exposing `set()` / `_jobs`:** tests seed settings/tokens via `set()` and force `started_at` via `_jobs` by design (these helpers are part of the dev/test adapter, not the production port surface).
