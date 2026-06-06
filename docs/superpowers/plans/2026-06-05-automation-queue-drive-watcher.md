# Automation, Advanced Queue & Provider Concurrency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add per-user auto-poll of Drive, a polling Drive watcher, advanced Redis queue keys with token-safe locks + a cloud semaphore, Postgres-gated retry/backoff, a dead-letter path, basic cost guardrails, and queue observability — without a 6th container and without breaking the legacy CLI/poll mode.

**Architecture:** Postgres stays source of truth; Redis = queue/locks/semaphore. Provider-aware concurrency (cloud default 5 / local 1) replaces the single global lock in redis-queue mode only. Retries are scheduled via `next_retry_at` in Postgres and swept by the reconciler. Auto-poll is a thread inside the worker.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy + PostgreSQL, Alembic, redis-py (lazy), pytest with dict-backed fakes + a `FakeRedis`.

**Ground rules every task:** TDD (failing test first), `.venv/bin/python -m pytest` to verify, frequent commits, no secrets in logs/UI, no sqlite, no stack traces in UI. Run `.venv/bin/python -m pytest -q` after each task; full validation in Task 16.

---

## File map

- `app/transcription/provider_kind.py` (new) — provider identity → cloud/local.
- `app/transcription/{deepgram_provider,faster_whisper_provider,whisper_cpp_provider}.py` — add `name`.
- `app/transcription/factory.py` — `resolve_provider` returns provider name too / classify helper.
- `app/errors.py` — `error_code`/`retryable` + new provider errors + `classify_error`.
- `app/deepgram_client.py` — map 429/401/413 to typed errors.
- `app/database/models.py` — `TranscriptionJob` new columns + indexes; new `UserAutomationSettings`.
- `alembic/versions/0002_automation_and_retry.py` (new).
- `app/core/models.py` — `Job` gains `next_retry_at`, `last_error_code`; new `AutomationSettings`.
- `app/core/ports.py` — extend `JobRepository`; new `AutomationSettingsRepository`; `Repositories.automation`.
- `app/repositories/memory.py`, `app/repositories/postgres.py` — implement the above.
- `app/queue/ports.py`, `app/queue/redis_queue.py`, `app/queue/memory_queue.py` — provider slots, dead/processing sets, stats, token-safe release (Lua).
- `app/queue/__init__.py` — `requeue_pending_jobs(repositories, queue, now=None)`.
- `app/queue/config.py` — semaphore caps + provider-lock TTL.
- `app/services/drive_watcher.py` (new) — multi-file watcher + guardrails.
- `app/drive_client.py` — `is_ready_media_file` (video+audio).
- `app/worker/config.py` — auto-poll/queue-threads/retry/guardrail settings.
- `app/worker/processor.py` — `resolve()` → `ResolvedProvider`; retry/terminal classification.
- `app/worker/queue_loop.py` — provider-slot acquisition + retry/dead-letter.
- `app/worker/auto_poll.py` (new) — poll loop thread.
- `app/worker/main.py` — start auto-poll + queue-consumer threads.
- `app/web/main.py` + templates — automation settings, check-now, retry, admin queue.
- `docker-compose.yml`, `.env.example`, `documentation/*`, `CLAUDE.md`, `overview/`.

---

## Task 1: Provider identity + cloud/local classification

**Files:** Create `app/transcription/provider_kind.py`; Modify the three provider classes; Test `tests/test_provider_kind.py`.

- [ ] **Step 1 — failing test** `tests/test_provider_kind.py`:
```python
from app.transcription.provider_kind import classify_provider_kind, CLOUD, LOCAL

def test_cloud_providers():
    for name in ("deepgram", "gemini", "openrouter"):
        assert classify_provider_kind(name) == CLOUD

def test_local_providers():
    for name in ("faster-whisper", "whisper-cpp"):
        assert classify_provider_kind(name) == LOCAL

def test_unknown_defaults_to_cloud():
    assert classify_provider_kind("mystery") == CLOUD
```
- [ ] **Step 2** Run `.venv/bin/python -m pytest tests/test_provider_kind.py -q` → FAIL (module missing).
- [ ] **Step 3 — implement** `app/transcription/provider_kind.py`:
```python
from __future__ import annotations

CLOUD = "cloud"
LOCAL = "local"
CLOUD_PROVIDERS = frozenset({"deepgram", "gemini", "openrouter"})
LOCAL_PROVIDERS = frozenset({"faster-whisper", "whisper-cpp"})

def classify_provider_kind(name: str | None) -> str:
    """Map a resolved provider's identity to a concurrency kind.

    Local CPU engines must serialize (kind=local); network providers may run in
    parallel (kind=cloud). Unknown names default to cloud (the cheaper side to
    overcommit; a genuinely broken job still fails terminally downstream)."""
    key = (name or "").strip().lower()
    if key in LOCAL_PROVIDERS:
        return LOCAL
    return CLOUD
```
- [ ] **Step 4** Add `name = "deepgram"` to `DeepgramProvider`, `name = "faster-whisper"` to `FasterWhisperProvider`, `name = "whisper-cpp"` to `WhisperCppProvider` (class attribute). Add a test asserting each instance `.name`.
- [ ] **Step 5** Run tests → PASS. **Commit** `feat(transcription): provider identity + cloud/local classification`.

## Task 2: Error classification (`error_code`/`retryable` + Deepgram HTTP mapping)

**Files:** Modify `app/errors.py`, `app/deepgram_client.py`; Test `tests/test_errors.py` (extend), `tests/test_deepgram_client.py` (extend).

- [ ] **Step 1 — failing test** in `tests/test_errors.py`:
```python
from app.errors import (AppError, classify_error, DeepgramRateLimitError,
                        ProviderKeyInvalidError, FileTooLargeError, ModelNotFoundError)

def test_apperror_defaults_terminal():
    e = AppError("x")
    assert e.error_code == "UNEXPECTED" and e.retryable is False

def test_rate_limit_is_retryable_with_after():
    e = DeepgramRateLimitError(retry_after_seconds=30)
    assert e.error_code == "RATE_LIMIT" and e.retryable is True
    code, retryable, after = classify_error(e)
    assert (code, retryable, after) == ("RATE_LIMIT", True, 30)

def test_key_invalid_is_terminal():
    code, retryable, after = classify_error(ProviderKeyInvalidError())
    assert retryable is False and code == "KEY_INVALID"

def test_unknown_exception_is_retryable():
    code, retryable, after = classify_error(ValueError("boom"))
    assert retryable is True and code == "UNEXPECTED"
```
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** in `app/errors.py`: add `error_code = "UNEXPECTED"` and `retryable = False` class attrs to `AppError`. Add classes:
  - `DeepgramRateLimitError(TranscriptionProviderError)`: `error_code="RATE_LIMIT"`, `retryable=True`, `__init__(self, *, retry_after_seconds=None, message=None)` storing `self.retry_after_seconds`.
  - `ProviderKeyInvalidError(TranscriptionProviderError)`: `error_code="KEY_INVALID"`, `retryable=False`, friendly pt-BR message.
  - `FileTooLargeError(TranscriptionProviderError)`: `error_code="FILE_TOO_LARGE"`, `retryable=False`.
  - Give `ModelNotFoundError`/`LocalTranscriptionConfigError`/`LocalTranscriptionUnavailableError`/`DeepgramKeyRequiredError` `error_code="CONFIG"`, `retryable=False`.
  Add:
```python
def classify_error(exc: BaseException) -> tuple[str, bool, int | None]:
    """(error_code, retryable, retry_after_seconds). Unknown exceptions retry."""
    if isinstance(exc, AppError):
        return exc.error_code, exc.retryable, getattr(exc, "retry_after_seconds", None)
    return "UNEXPECTED", True, None
```
  Update `__all__`.
- [ ] **Step 4 — Deepgram mapping test** in `tests/test_deepgram_client.py`: a fake session returning `status_code=429` (with `headers={"Retry-After": "12"}`) → `DeepgramRateLimitError` with `retry_after_seconds==12`; `401`/`403` → `ProviderKeyInvalidError`; `413` → `FileTooLargeError`; other non-2xx → `DeepgramError` (unchanged).
- [ ] **Step 5 — implement** in `app/deepgram_client.py` `transcribe`: before the generic raise, branch on `response.status_code`:
```python
status = response.status_code
if status == 429:
    raise DeepgramRateLimitError(
        retry_after_seconds=_retry_after(response),
        message=f"Deepgram rate limited: {status}",
    )
if status in (401, 403):
    raise ProviderKeyInvalidError(f"Deepgram auth failed: {status}")
if status == 413:
    raise FileTooLargeError(f"Deepgram file too large: {status}")
if not 200 <= status < 300:
    raise DeepgramError(...)  # unchanged
```
  with `_retry_after(resp)` parsing `resp.headers.get("Retry-After")` to int or None (guard missing `headers`). Import the new errors.
- [ ] **Step 6** Run both test files → PASS. **Commit** `feat(errors): retryable classification + Deepgram 429/401/413 mapping`.

## Task 3: DB model columns + indexes + `user_automation_settings` + Alembic 0002

**Files:** Modify `app/database/models.py`; Create `alembic/versions/0002_automation_and_retry.py`; Test `tests/test_models_metadata.py` (extend).

- [ ] **Step 1 — failing test** in `tests/test_models_metadata.py`:
```python
from app.database.models import Base, TranscriptionJob, UserAutomationSettings

def test_job_has_retry_columns():
    cols = TranscriptionJob.__table__.c
    assert "next_retry_at" in cols and "last_error_code" in cols

def test_job_has_new_indexes():
    names = {ix.name for ix in TranscriptionJob.__table__.indexes}
    assert "ix_transcription_jobs_status_next_retry" in names
    assert "ix_transcription_jobs_user_created" in names

def test_automation_settings_table():
    t = UserAutomationSettings.__table__
    for c in ("user_id","auto_poll_enabled","poll_interval_seconds","max_files_per_poll",
              "last_poll_at","last_success_at","last_error_code","last_error_message",
              "daily_jobs_limit","max_file_size_mb","monthly_cloud_minutes_limit",
              "max_file_duration_minutes"):
        assert c in t.c
    assert {ix.name for ix in t.indexes} >= {"ix_user_automation_enabled_polled"}
```
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** in `app/database/models.py`:
  - On `TranscriptionJob`: add `next_retry_at: Mapped[datetime|None]` (`DateTime(timezone=True)`, nullable), `last_error_code: Mapped[str|None]` (`Text`, nullable). Append to `__table_args__`: `Index("ix_transcription_jobs_status_next_retry", "status", "next_retry_at")`, `Index("ix_transcription_jobs_user_created", "user_id", "created_at")`.
  - Add class `UserAutomationSettings(TimestampMixin, Base)` `__tablename__="user_automation_settings"` with `id` PK; `user_id` FK→users.id CASCADE, unique, index; `auto_poll_enabled Boolean server_default false`; `poll_interval_seconds Integer server_default '300'`; `max_files_per_poll Integer server_default '5'`; `last_poll_at`/`last_success_at` DateTime tz nullable; `last_error_code Text nullable`; `last_error_message Text nullable`; `daily_jobs_limit`/`max_file_size_mb`/`monthly_cloud_minutes_limit`/`max_file_duration_minutes` Integer nullable; `__table_args__=(Index("ix_user_automation_enabled_polled","auto_poll_enabled","last_poll_at"),)`.
- [ ] **Step 4** Run model test → PASS.
- [ ] **Step 5 — migration** `alembic/versions/0002_automation_and_retry.py`: `revision="0002_automation_and_retry"`, `down_revision="0001_initial"` (confirm the exact 0001 revision id by reading `0001_create_initial_postgres_schema.py`). `upgrade()`: `op.add_column("transcription_jobs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))`, same for `last_error_code` (`sa.Text`); `op.create_index("ix_transcription_jobs_status_next_retry","transcription_jobs",["status","next_retry_at"])`; `op.create_index("ix_transcription_jobs_user_created","transcription_jobs",["user_id","created_at"])`; `op.create_table("user_automation_settings", ...)` mirroring the ORM exactly (server defaults via `sa.text`); `op.create_index("ix_user_automation_enabled_polled", ...)`. `downgrade()`: reverse (drop index, drop table, drop indexes, drop columns).
- [ ] **Step 6** `.venv/bin/python -m compileall alembic/versions/0002_automation_and_retry.py` → OK. **Commit** `feat(db): job retry columns, automation settings table, migration 0002`.

## Task 4: Job domain fields + JobRepository retry/guardrail/observability methods

**Files:** Modify `app/core/models.py`, `app/web/repositories.py` (web `Job`), `app/core/ports.py`, `app/repositories/memory.py`, `app/repositories/postgres.py`, `tests/test_core_ports.py` (`_Stub`); Test `tests/test_repositories_memory.py` (extend).

- [ ] **Step 1 — failing tests** in `tests/test_repositories_memory.py` (use `build_memory_repositories`):
  - `schedule_retry` sets status=pending, `next_retry_at`, `last_error_code`, keeps `attempts` and `source_file_id`.
  - `claim_job`/`claim_next_pending_job` return None when `next_retry_at > now`, and the job once `now >= next_retry_at`.
  - `list_pending_jobs(now)` excludes jobs with `next_retry_at > now`; `list_pending_jobs()` (no arg) returns all pending (back-compat).
  - `count_jobs_created_since(user_id, since)` counts only that user's jobs with `created_at >= since`.
  - `mark_failed(..., error_code="KEY_INVALID")` stores `last_error_code`.
  - `reset_job_for_retry(job_id, now)` resets failed→pending, attempts=0, clears `next_retry_at`/error.
  - `count_jobs_by_status()` returns a dict like `{"pending":1,"failed":2}`.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement**:
  - `app/core/models.py` `Job`: add `next_retry_at: datetime|None=None`, `last_error_code: str|None=None`.
  - `app/web/repositories.py` `Job`: add `last_error_code: str|None=None` (web view; ISO strings already).
  - `app/core/ports.py` `JobRepository`: add method stubs/docstrings: `schedule_retry(job_id, now, next_retry_at, error_code, error_message)`, `count_jobs_created_since(user_id, since)`, `count_jobs_by_status()`, `reset_job_for_retry(job_id, now)`; change `mark_failed(self, job_id, error_message, now, error_code=None)`; change `list_pending_jobs(self, now=None)`; document that `claim_*` skip `next_retry_at > now`.
  - `app/repositories/memory.py` `InMemoryJobRepository`: implement all; in `claim_*` add guard `if job.next_retry_at and job.next_retry_at > now: skip/return None`; `list_pending_jobs(now=None)` filters; `_to_job`/Job copies carry new fields automatically (dataclass).
  - `app/repositories/postgres.py`: extend `_to_job` with `next_retry_at`, `last_error_code`; in `claim_next_pending_job`/`claim_job` add SQL predicate `or_(TranscriptionJob.next_retry_at.is_(None), TranscriptionJob.next_retry_at <= now)`; `list_pending_jobs(now=None)` adds the same predicate when `now` given; implement `schedule_retry` (status=pending, set next_retry_at/last_error_code/error_message, keep attempts), `mark_failed(..., error_code=None)` (set last_error_code), `count_jobs_created_since` (`select(func.count())...where(user_id, created_at>=since)`), `count_jobs_by_status` (`group_by(status)`), `reset_job_for_retry` (status=pending, attempts=0, next_retry_at=None, error_message=None, last_error_code=None, updated_at=now).
  - `tests/test_core_ports.py` `_Stub`: add `schedule_retry`, `count_jobs_created_since`, `count_jobs_by_status`, `reset_job_for_retry` methods (`def ...(self,*a,**k): ...`).
- [ ] **Step 4** Run `tests/test_repositories_memory.py` + `tests/test_core_ports.py` → PASS. **Commit** `feat(jobs): retry scheduling, guardrail counts, observability counts (both adapters)`.

## Task 5: AutomationSettings model + repository (worker bundle)

**Files:** Modify `app/core/models.py`, `app/core/ports.py`, `app/repositories/memory.py`, `app/repositories/postgres.py`, `app/database/repositories.py` (if the canonical layer wraps tables; otherwise query models directly in postgres adapter); Test `tests/test_automation_repository.py` (new, memory).

- [ ] **Step 1 — failing test** `tests/test_automation_repository.py` using `build_memory_repositories().automation`:
  - `get_for_user` returns None initially.
  - `upsert_for_user(user_id, auto_poll_enabled=True, poll_interval_seconds=120, ...)` then `get_for_user` returns those values.
  - second `upsert_for_user` updates in place (no duplicate).
  - `list_due_for_poll(now, limit)` returns only enabled users whose `last_poll_at` is None or `<= now - poll_interval_seconds`, capped at `limit`, and excludes a user polled recently.
  - `mark_poll_result(user_id, now, success=True)` sets `last_poll_at` + `last_success_at`; `mark_poll_result(user_id, now, success=False, error_code="DRIVE", error_message="...")` sets error fields + `last_poll_at` (not success).
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement**:
  - `app/core/models.py`: add `@dataclass AutomationSettings` with `user_id`, `auto_poll_enabled`, `poll_interval_seconds`, `max_files_per_poll`, `last_poll_at`, `last_success_at`, `last_error_code`, `last_error_message`, and the four guardrail ints, all with sensible defaults.
  - `app/core/ports.py`: add `@runtime_checkable AutomationSettingsRepository(Protocol)` with `get_for_user`, `upsert_for_user(self, user_id, **fields)`, `list_due_for_poll(self, now, limit)`, `mark_poll_result(self, user_id, now, *, success, error_code=None, error_message=None)`. Add `automation: AutomationSettingsRepository | None = None` (default) to `Repositories`.
  - `app/repositories/memory.py`: `InMemoryAutomationSettingsRepository` (dict by user_id, threading.Lock); add to `build_memory_repositories(... automation=...)`.
  - `app/repositories/postgres.py`: `PgAutomationSettingsRepository(_Bound)` over `models.UserAutomationSettings`; `upsert_for_user` = get-or-create then set fields; `list_due_for_poll` = `select(...).where(auto_poll_enabled.is_(True))` then filter due in Python (or SQL interval); add to `build_postgres_repositories`.
- [ ] **Step 4** Run → PASS. **Commit** `feat(automation): AutomationSettings model + repository in both adapters`.

## Task 6: Queue — provider slots, dead/processing sets, stats, token-safe release

**Files:** Modify `app/queue/ports.py`, `app/queue/redis_queue.py`, `app/queue/memory_queue.py`; Test `tests/test_queue_memory.py` (extend), `tests/test_queue_redis.py` (extend `FakeRedis`).

- [ ] **Step 1 — failing tests** (memory queue, deterministic): in `tests/test_queue_memory.py`:
  - cloud cap from a constructor arg `cloud_concurrency=2`: 2 `acquire_provider_slot("cloud", ttl)` return tokens; 3rd returns None; after `release_provider_slot("cloud", token)` a new acquire succeeds.
  - local cap 1: 1st acquire returns token; 2nd None; release frees it.
  - `mark_processing`/`clear_processing` reflected in `queue_stats()["processing"]`; `mark_dead`/`remove_dead` reflected in `queue_stats()["dead"]` and `dead_job_ids()`.
  In `tests/test_queue_redis.py`: extend `FakeRedis` with zset ops (`zadd`,`zcard`,`zrem`,`zremrangebyscore`,`zrange`) + a `register_script(src)` returning a callable that runs the Python equivalent; assert cloud semaphore: 5 acquire / 6th None / release frees / an expired slot (score<now) is reclaimed on next acquire; assert token-safe release no-ops on a foreign token.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** `app/queue/ports.py`: add to the Protocol: `acquire_provider_slot(self, kind, ttl_seconds) -> str | None`, `release_provider_slot(self, kind, token) -> None`, `mark_processing(self, job_id)`, `clear_processing(self, job_id)`, `mark_dead(self, job_id)`, `remove_dead(self, job_id)`, `dead_job_ids(self) -> set[int]`, `queue_stats(self) -> dict[str,int]`.
- [ ] **Step 4 — implement** `app/queue/redis_queue.py`:
  - `__init__(..., cloud_concurrency=5)`: store caps; keys `self._processing_key=f"{queue_name}:processing"`, `self._dead_key=f"{queue_name}:dead"`, `self._local_lock_key="lock:local"`, `self._cloud_sem_key="semaphore:cloud"`. Lazily `register_script` for: cloud acquire, and compare-and-del release. Guard `register_script` missing on fakes by falling back to a Python path.
  - Cloud acquire Lua (atomic): `ZREMRANGEBYSCORE key -inf now; if ZCARD < cap then ZADD key expiry token; return token else return false`.
  - `acquire_provider_slot(kind, ttl)`: local → `set(self._local_lock_key, token, nx=True, ex=ttl)`; cloud → run the script with `keys=[cloud_sem_key]`, `args=[now, now+ttl, cap, token]`.
  - `release_provider_slot(kind, token)`: local → compare-and-del Lua on `lock:local`; cloud → `ZREM cloud_sem_key token`.
  - `mark_processing`/`clear_processing` = `SADD`/`SREM` on processing set; `mark_dead`/`remove_dead` = `SADD`/`SREM` on dead set; `dead_job_ids` = `SMEMBERS`→ints; `queue_stats` = `{"queued": LLEN list, "processing": SCARD processing, "dead": SCARD dead}`. `now` via an injected `time_fn` (default `time.time`) so tests are deterministic.
  - Make `release_global_lock` use the same compare-and-del Lua (atomic) — keep behavior, improve safety.
- [ ] **Step 5 — implement** `app/queue/memory_queue.py`: add `cloud_concurrency=5`; `self._cloud=set()` tokens with cap, `self._local_token=None`, `self._processing=set()`, `self._dead=set()`; token-safe release; `queue_stats`/`dead_job_ids`. TTL ignored (parity).
- [ ] **Step 6** Run both queue test files → PASS. **Commit** `feat(queue): provider slots (cloud semaphore/local lock), dead+processing sets, stats, atomic release`.

## Task 7: `requeue_pending_jobs` respects `next_retry_at`

**Files:** Modify `app/queue/__init__.py`; Test `tests/test_queue_requeue.py` (extend).

- [ ] **Step 1 — failing test**: seed two pending jobs, one with `next_retry_at` in the future; `requeue_pending_jobs(repos, queue, now=fixed_now)` enqueues only the due one. Back-compat: with no `now`, both enqueue.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement**: `def requeue_pending_jobs(repositories, queue, now=None)`: `jobs = repositories.jobs.list_pending_jobs(now) if now is not None else repositories.jobs.list_pending_jobs()`; loop `ensure_queued`. (Relies on Task 4's `list_pending_jobs(now)` gating.)
- [ ] **Step 4** Run → PASS. **Commit** `feat(queue): reconciler skips jobs still in retry backoff`.

## Task 8: Drive watcher service (multi-file) + audio filter

**Files:** Create `app/services/drive_watcher.py`; Modify `app/drive_client.py` (`is_ready_media_file`); Test `tests/test_drive_watcher.py` (new), `tests/test_drive_client.py` (extend for audio).

- [ ] **Step 1 — failing test** `tests/test_drive_watcher.py` (memory repos + `FakeDriveClient` from `tests/support.py`, seed settings+token):
  - `poll_user(...)` with 3 new files and `max_files=5` creates 3 pending jobs and returns a result with `created==3`.
  - dedupe: a file already `completed`/`pending` is skipped.
  - `max_files=1` creates only 1.
  - a `FakeDriveClient.list_video_files` raising → result `created==0` with a friendly `error_code`/message (no traceback).
  audio test in `tests/test_drive_client.py`: `is_ready_media_file({"mimeType":"audio/mpeg","name":"a.mp3","size":"5"})` is True; `.wav`/`.m4a` by name True; trashed/zero-size False.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** `app/drive_client.py`: rename/extend filter to `is_ready_media_file(item)` accepting video/mp4 + audio mimes (`audio/mpeg`,`audio/mp3`,`audio/wav`,`audio/x-wav`,`audio/mp4`,`audio/m4a`,`audio/flac`) and extensions (`.mp4`,`.mp3`,`.wav`,`.m4a`,`.flac`,`.ogg`); keep `is_ready_video_file` as a thin alias for back-compat; `list_video_files` calls the new filter.
- [ ] **Step 4 — implement** `app/services/drive_watcher.py`:
```python
@dataclass(frozen=True)
class PollResult:
    created: int
    skipped: int
    error_code: str | None = None
    error_message: str | None = None
    job_ids: tuple[int, ...] = ()

def poll_user(repositories, build_drive_client, credentials_from_token, user_id, *,
              now, max_files, guardrails=None) -> PollResult:
    # settings/token preconditions -> friendly error_code (NO_SETTINGS/NOT_CONNECTED)
    # build drive client; try list; on exception -> PollResult(0,0,"DRIVE_ERROR", friendly)
    # for each file (cap max_files): guardrail checks (size); find_existing_job dedupe;
    # create_job + enqueue is the caller's job? -> return job ids; enqueue handled by caller.
```
  Keep enqueue in the caller (worker/web) so the service stays pure/testable. Return created job ids; caller enqueues each. Guardrails param is a callable/struct (Task 9) — default no-op.
- [ ] **Step 5** Run → PASS. **Commit** `feat(watcher): multi-file Drive poll service + audio support`.

## Task 9: Cost guardrails at job creation

**Files:** Modify `app/services/drive_watcher.py`, `app/worker/config.py` (defaults); Test `tests/test_guardrails.py` (new).

- [ ] **Step 1 — failing test**: `poll_user` with `guardrails=Guardrails(max_file_size_mb=1, daily_jobs_limit=2)`:
  - a file with `size > 1MB` is skipped with a "Arquivo excede limite permitido." reason and not created.
  - once `count_jobs_created_since(user_id, midnight)` ≥ `daily_jobs_limit`, no more jobs are created and result carries "Limite diário de jobs atingido.".
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** a `Guardrails` dataclass (resolved from per-user `AutomationSettings` overrides else global env defaults) with `allow_file(file) -> (ok, message)` (size) and a daily-count check using `repositories.jobs.count_jobs_created_since`. Wire into `poll_user`. Add `MAX_FILE_SIZE_MB`, `DAILY_JOBS_LIMIT` (0/empty = unlimited) to `WorkerSettings`.
- [ ] **Step 4** Run → PASS. **Commit** `feat(guardrails): enforce file-size + daily job limit at creation`.

## Task 10: Worker queue loop — provider-aware concurrency + retry/dead-letter

**Files:** Modify `app/worker/processor.py`, `app/worker/queue_loop.py`, `app/worker/config.py`; Test `tests/test_worker_queue_loop.py` (rewrite for slots), `tests/test_worker_processor.py` (retry/terminal).

- [ ] **Step 1 — failing tests**:
  - processor: a transient failure (`DeepgramRateLimitError`) on a job with `attempts < max` calls `schedule_retry` with a future `next_retry_at` (backoff) and keeps `source_file_id`; a terminal failure (`ProviderKeyInvalidError`) calls `mark_failed(error_code="KEY_INVALID")` + `queue.mark_dead`; reaching `attempts >= JOB_MAX_ATTEMPTS` dead-letters even for a retryable error.
  - queue loop: `run_queue_loop` acquires a **cloud** slot for a deepgram-resolved job and a **local** slot for a whisper-resolved job; when `acquire_provider_slot` returns None it `requeue`s and backs off (job NOT failed); `mark_processing`/`clear_processing` bracket the work; slot released in `finally`.
  Use a fake queue recording slot calls + a stub processor exposing `resolve(job)->ResolvedProvider(kind=...)`.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** `app/worker/processor.py`:
  - Add `@dataclass ResolvedProvider: provider; name; kind; status`. `def resolve(self, job) -> ResolvedProvider`: load settings/token (raise terminal on missing), resolve provider, derive `name` (`getattr(provider,"name",...)` or `status.summary`/"deepgram"), `kind = classify_provider_kind(name)`.
  - Refactor `process(self, job, resolved=None)`: if `resolved` None, call `self.resolve(job)`; use `resolved.provider`. Replace the single `except` mark_failed with retry logic:
```python
except Exception as exc:
    code, retryable, after = classify_error(exc)
    user_message = exc.user_message if isinstance(exc, AppError) else str(exc)
    LOGGER.exception("Transcription failed: job_id=%s code=%s", job.id, code)
    attempts = job.attempts  # already incremented at claim
    if retryable and attempts < self.container.settings.job_max_attempts:
        delay = _backoff(attempts, base, maxs, after)
        repos.jobs.schedule_retry(job.id, self._now(),
                                  next_retry_at=self._now()+timedelta(seconds=delay),
                                  error_code=code, error_message=user_message)
    else:
        repos.jobs.mark_failed(job.id, user_message, self._now(), error_code=code)
        if self.container.queue is not None:
            self.container.queue.mark_dead(job.id)
```
  - `_backoff(attempts, base, maxs, retry_after)` = `max(retry_after or 0, min(maxs, base * 2**(attempts-1)))`.
- [ ] **Step 4 — implement** `app/worker/queue_loop.py`: replace global-lock block with: `resolved = proc.resolve(job)`; `token = queue.acquire_provider_slot(resolved.kind, container.provider_lock_ttl)`; if None → `queue.requeue(job_id)`, `contention()`, continue; else `queue.mark_processing(job.id)`; `try: proc.process(job, resolved) finally: queue.clear_processing(job.id); queue.release_provider_slot(resolved.kind, token)`. Resolve errors (terminal) caught → `mark_failed` (no slot). Keep the outer try/except backoff.
- [ ] **Step 5 — config** `app/worker/config.py`: add `job_max_attempts`, `job_retry_base_seconds`, `job_retry_max_seconds`, `provider_lock_ttl_seconds`, `queue_concurrency` (TRANSCRIPTION_QUEUE_CONCURRENCY). Add `provider_lock_ttl` to `WorkerContainer` (from `QueueSettings`).
- [ ] **Step 6** Run worker tests → PASS. **Commit** `feat(worker): provider-aware slots + Postgres-gated retry + dead-letter`.

## Task 11: Auto-poll loop thread

**Files:** Create `app/worker/auto_poll.py`; Modify `app/worker/main.py`, `app/worker/config.py`; Test `tests/test_auto_poll.py` (new).

- [ ] **Step 1 — failing tests** (memory repos + fake queue + injected `now`):
  - one tick with an enabled, due user + new Drive files creates jobs, enqueues them, and records `last_poll_at`/`last_success_at`.
  - a completed/pending source is not duplicated.
  - when `acquire(lock:auto_poll)` returns None (another poller), the tick does nothing.
  - a Drive error records friendly `last_error_code`/`last_error_message` and does not raise.
  - `lock:auto_poll` is released at end of tick.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** `app/worker/auto_poll.py`: `run_auto_poll_loop(container, stop_event, now=_utc_now)` looping until stop; each tick `_auto_poll_tick(container, now)`: acquire `lock:auto_poll` via `queue.acquire_provider_slot`? No — use a dedicated `queue.acquire_named_lock`? Simpler: reuse `acquire_provider_slot` is provider-specific; instead acquire via `queue` global-lock-style helper. Add a tiny generic `acquire_named_lock(name, ttl)`/`release_named_lock(name, token)` to the queue Protocol+adapters in this task (memory+redis, token-safe) for `lock:auto_poll`. Tick: if lock acquired → `requeue_pending_jobs(repos, queue, now())`; `for s in repos.automation.list_due_for_poll(now(), max_users)`: `poll_user(...)`, enqueue returned job ids, `repos.automation.mark_poll_result(...)`; finally release lock. Catch per-user errors → friendly mark_poll_result; never raise out of the thread.
- [ ] **Step 4 — wire** `app/worker/main.py` `run()`: after reconcile, if `container.settings.auto_poll_enabled and container.queue is not None`, start a daemon thread `run_auto_poll_loop`; spawn `queue_concurrency` consumer threads (queue mode) instead of `concurrency`. Poll mode unchanged. Add `auto_poll_*` to `WorkerSettings`.
- [ ] **Step 5** Run → PASS. **Commit** `feat(worker): auto-poll loop thread with single-poller lock + retry sweep`.

## Task 12: Config/env + docker-compose + .env.example

**Files:** Modify `app/worker/config.py`, `app/queue/config.py`, `docker-compose.yml`, `.env.example`; Test `tests/test_worker_config.py`, `tests/test_queue_config.py` (extend).

- [ ] **Step 1 — failing tests**: `WorkerSettings.from_env({...})` parses the new vars with defaults; `QueueSettings.from_env` parses `CLOUD_TRANSCRIPTION_CONCURRENCY`/`LOCAL_TRANSCRIPTION_CONCURRENCY`/`PROVIDER_LOCK_TTL_SECONDS`.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** the `from_env` additions (use the existing `_positive_int` helper / `parse_bool`). `build_queue`/`RedisTranscriptionQueue` receive `cloud_concurrency` from `QueueSettings`. Add all vars to `docker-compose.yml` (worker env, `${VAR:-default}`) and document in `.env.example` with comments.
- [ ] **Step 4** Run config tests; `docker compose config` (needs `.env`; `cp .env.example .env` if missing) → OK. **Commit** `feat(config): auto-poll/concurrency/retry/guardrail env + compose defaults`.

## Task 13: Web — automation settings, check-now, retry, admin queue

**Files:** Modify `app/web/main.py`; Create templates `automation_settings.html`, `queue_status.html`; Modify `jobs.html`, `settings.html`, `base.html`; Test `tests/test_web_routes.py` (extend).

- [ ] **Step 1 — failing tests** (TestClient): GET `/settings/automation` renders current settings; POST saves (toggle + interval + max files) via `app.state.worker_repositories.automation`; POST `/automation/check-now` calls the watcher and flashes a result; POST `/jobs/{id}/retry` on a failed user-owned job resets it to pending + enqueues + `remove_dead`; GET `/admin/queue` (admin) shows `queue_stats`; non-admin → 403/redirect.
- [ ] **Step 2** Run → FAIL.
- [ ] **Step 3 — implement** routes in `create_app` mirroring `/settings/deepgram` + `/jobs/run-once` patterns; reach automation repo via the existing `_resolve_worker_repositories()`; check-now/retry enqueue through `app.state.queue` (best-effort try/except). Templates extend `base.html`, no CDN. Nav + settings links added.
- [ ] **Step 4** Run web tests → PASS. **Commit** `feat(web): automation settings UI, check-now, dead-letter retry, admin queue panel`.

## Task 14: Docs + CLAUDE.md

**Files:** Create `documentation/28..32`; Modify `README.md`, `.env.example` (final pass), `documentation/03,09,11,19`, `CLAUDE.md`.

- [ ] **Step 1** Write `documentation/28-auto-polling.md`, `29-redis-queue-advanced.md`, `30-provider-concurrency.md`, `31-retries-dead-letter.md`, `32-cost-guardrails.md`.
- [ ] **Step 2** Update `README.md` feature list + env; `03-environment-variables.md` (all new vars); `09-redis-queue.md` (new keys/semaphore); `11-worker-flow.md` (auto-poll + slots + retry); `19-roadmap.md` (Changes API + `drive_watch_state` next steps).
- [ ] **Step 3** Update `CLAUDE.md` concurrency hard rule → cloud configurable (default 5) / local 1 / Postgres source of truth / Redis queue+locks+semaphore.
- [ ] **Step 4** **Commit** `docs: automation, advanced queue, concurrency, retries, guardrails + CLAUDE.md`.

## Task 15: Overview

**Files:** Create `overview/feat-automation-queue-drive-watcher.md`.

- [ ] **Step 1** Write the overview per the prompt's 14-point template (branch, goal, files created/changed, migrations, env vars, tests, commands, results, risks, manual test, next steps, PR link, and the three confirmations: no SQLite, no secret logging, no heavy transcription in Web UI). **Commit** `docs: task overview`.

## Task 16: Full validation + PR

- [ ] **Step 1** `.venv/bin/python -m pytest -v` → all pass.
- [ ] **Step 2** `.venv/bin/python -m compileall app scripts alembic` → OK.
- [ ] **Step 3** `docker compose config` and `docker compose build` → OK.
- [ ] **Step 4** Update overview with results; `git add . && git commit`; `git push -u origin feat/automation-queue-drive-watcher`.
- [ ] **Step 5** `gh pr create --base main --head feat/automation-queue-drive-watcher --title "Add automatic Drive polling and provider queue policies" --body "..."`; record the PR link in the overview; commit.

---

## Self-review notes

- **Spec coverage:** auto-poll (T11), watcher (T8), Changes API deferred (T14 docs), advanced queue keys (T6), requeue-respecting-backoff (T7), provider concurrency (T6+T10), retry/backoff (T2+T4+T10), dead-letter (T6+T10+T13), guardrails (T9), observability (T6+T13), tests (each task), docs (T14), overview (T15). ✓
- **Type consistency:** `acquire_provider_slot(kind, ttl)`/`release_provider_slot(kind, token)`, `ResolvedProvider(provider,name,kind,status)`, `classify_provider_kind`, `PollResult`, `schedule_retry`, `count_jobs_created_since` used identically across tasks. `list_pending_jobs(now=None)` back-compat preserved. `Repositories.automation` optional default so existing constructions don't break.
- **Index note:** `(user_id, source_file_id)` already exists as `ix_transcription_jobs_user_source`; not re-created.
- **No-placeholder check:** critical code (Lua semaphore, retry/backoff, classification, slot loop) is spelled out; CRUD mirrors shown adapters.
