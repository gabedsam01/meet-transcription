# Design — Automation, Advanced Redis Queue, Drive Watcher & Provider Concurrency

- **Date:** 2026-06-05
- **Branch:** `feat/automation-queue-drive-watcher`
- **Status:** Approved (design + 4 mandatory adjustments)

## 1. Goal

Add the automation layer on top of the existing PostgreSQL-authoritative,
Redis-queue worker:

1. Per-user auto-poll of a Drive folder.
2. A polling Drive watcher (Changes API deferred → next step).
3. Advanced Redis queue keys (queued/processing/dead) + safe locks/semaphores.
4. `requeue_pending_jobs` that respects retry backoff.
5. Provider-aware concurrency: cloud configurable (default 5), local CPU = 1.
6. Retry/backoff with `next_retry_at`.
7. Dead-letter (status=failed + attempts exhausted) with a UI "retry".
8. Basic cost/quota guardrails enforced at job creation.
9. Queue observability (admin panel).

No 6th container. Five services stay: postgres, redis, migrate, web, worker.

## 2. Hard rules honored

- PostgreSQL is the single source of truth. Redis = queue/locks/semaphore only;
  anything Redis loses is recoverable from Postgres (`requeue_pending_jobs`).
- No SQLite / `sqlite3` / `app.db` / `database_path`. Dict-backed fakes in tests.
- Web never transcribes/downloads heavy video; it only validates, creates
  `pending` jobs, and enqueues ids. (`/automation/check-now` is a lightweight
  Drive *listing* + job creation, same class of work as today's `/jobs/run-once`.)
- Tokens/keys stay Fernet-encrypted at rest; secrets never reach logs/UI/errors.
- No stack traces in the UI — `AppError.user_message` only.
- `QUEUE_BACKEND=none` poll mode and the legacy `python -m app.main` CLI stay
  strictly one-at-a-time and are untouched.

## 3. Confirmed decisions

- Concurrency: **provider-aware, cloud default 5 / local 1**, replacing the single
  global execution lock **in redis-queue mode only**. Global-lock methods are
  retained on the queue Protocol (back-compat).
- Retry scheduling: **Postgres-gated `next_retry_at`** (survives a full Redis wipe).
- Scope: **MVP + scaffold**. Drive Changes API + `drive_watch_state` table +
  minute-metering = documented next steps.
- `CLAUDE.md` concurrency hard rule will be rewritten (see §13).
- Unknown/unexpected exceptions are **retryable** up to `JOB_MAX_ATTEMPTS`, then
  dead-letter. Known-terminal / invalid-config / invalid-key errors **never** retry.
  429 / rate-limit retries with backoff.

### Mandatory adjustments (from review)

1. **Classify by the resolved provider**, not by "local is valid". Map provider
   identity → kind: `{deepgram, gemini, openrouter, …} → cloud`;
   `{faster-whisper, whisper-cpp} → local`. A user with a valid local engine who
   *chose* Deepgram is a **cloud** job.
2. **Token-safe Redis primitives**: locks acquire with a unique token and release
   only if the token still owns the key (atomic compare-and-del via Lua). The cloud
   ZSET semaphore acquire+reclaim is a **single atomic Lua script** — no race where
   5 workers grab the same slot.
3. **`requeue_pending_jobs(now)`** re-enqueues only `status=pending AND
   (next_retry_at IS NULL OR next_retry_at <= now)`. Jobs still in backoff are skipped.
4. **Indexes** (added in migration + ORM):
   - `transcription_jobs(status, next_retry_at)`
   - `transcription_jobs(user_id, created_at)`
   - `transcription_jobs(user_id, source_file_id)` (reuse existing `user_source` if present)
   - `user_automation_settings(auto_poll_enabled, last_poll_at)`

## 4. Data model — Alembic `0002` (down_revision `0001_initial`, hand-written up/down)

**`transcription_jobs`** — add columns + indexes:
- `next_retry_at TIMESTAMPTZ NULL`
- `last_error_code VARCHAR NULL`
- (existing `attempts INTEGER`, `error_message TEXT` reused — `error_message` keeps
  the friendly message)
- new indexes: `(status, next_retry_at)`, `(user_id, created_at)`; ensure
  `(user_id, source_file_id)` index exists.

**`user_automation_settings`** (new, 1:1 with `users`, FK CASCADE, unique `user_id`):
- `id` PK
- `user_id` FK → users.id, unique
- `auto_poll_enabled BOOLEAN NOT NULL DEFAULT false`
- `poll_interval_seconds INTEGER NOT NULL DEFAULT 300`
- `max_files_per_poll INTEGER NOT NULL DEFAULT 5`
- `last_poll_at TIMESTAMPTZ NULL`
- `last_success_at TIMESTAMPTZ NULL`
- `last_error_code VARCHAR NULL`
- `last_error_message TEXT NULL`
- guardrails (NULL = use global env default): `daily_jobs_limit INTEGER NULL`,
  `max_file_size_mb INTEGER NULL`, `monthly_cloud_minutes_limit INTEGER NULL`,
  `max_file_duration_minutes INTEGER NULL`
- `created_at`/`updated_at` (TimestampMixin)
- index `(auto_poll_enabled, last_poll_at)`

**`drive_watch_state`** — **deferred** (polling MVP). Documented in `28`/`19-roadmap`.

## 5. Provider classification (`app/transcription/`)

- Give providers an identity: `DeepgramProvider.name="deepgram"`,
  `FasterWhisperProvider.name="faster-whisper"`, `WhisperCppProvider.name="whisper-cpp"`.
- `CLOUD_PROVIDERS = {"deepgram", "gemini", "openrouter"}`,
  `LOCAL_PROVIDERS = {"faster-whisper", "whisper-cpp"}`;
  `classify_provider_kind(name) -> "cloud" | "local"` (default unknown → cloud, the
  cheaper-to-overcommit side; terminal failures still caught).
- `JobProcessor.resolve(job) -> ResolvedProvider(provider, name, kind, status)` loads
  settings + token + deepgram-key presence and resolves the real provider. The queue
  loop classifies via `resolved.kind`, acquires the matching slot, then calls
  `processor.process(job, resolved)` (no double resolve). A terminal resolution
  error (no provider available / key invalid) → `mark_failed` (no slot, no retry).

## 6. Drive watcher (polling MVP) — `app/services/drive_watcher.py`

Factor the shared "list folder → filter ready media → dedupe by
`find_existing_job(user_id, source_file_id, BLOCKING_STATUSES)` → create `pending`
→ enqueue" logic so it can create **up to N** jobs per user. `is_ready_video_file`
extended to accept common audio mime/extensions (mp3/m4a/wav/…). Guardrails applied
per file before creation (§9). `create_next_pending_job` keeps its current
single-job behavior/signature (back-compat: `/jobs/run-once`, `test_job_service.py`).

## 7. Auto-poll loop — thread in the worker — `app/worker/auto_poll.py`

Started from `worker/main.py:run()` **after** `recover_stale_jobs()` +
`requeue_pending_jobs()`, only if `AUTO_POLL_ENABLED`. Each tick:
1. Acquire Redis `lock:auto_poll` (SET NX EX, `AUTO_POLL_LOCK_TTL_SECONDS`, unique
   token). If not acquired → another poller active → skip tick.
2. `requeue_pending_jobs(now)` — sweep due retry jobs on a bounded cadence even
   under load.
3. Enumerate ≤ `AUTO_POLL_MAX_USERS_PER_TICK` users with `auto_poll_enabled` whose
   `last_poll_at` is NULL or older than their `poll_interval_seconds`.
4. Per user: run the watcher (≤ `max_files_per_poll`); record `last_poll_at` +
   `last_success_at`, or friendly `last_error_code`/`last_error_message`.
5. Release `lock:auto_poll` (token-checked).

Transient errors → short `warning` + continue; **thread never dies**. Redis down →
`warning` + retry next tick. BRPOP idle timeouts are normal, logged at debug, never ERROR.

## 8. Advanced queue + safe concurrency — `app/queue/`

New Redis keys: `<name>:processing` (set), `<name>:dead` (set),
`lock:auto_poll`, `lock:local`, `semaphore:cloud` (ZSET of slot tokens scored by
expiry). Existing `<name>:queue` (list), `<name>:queued` (set), global lock retained.

New Protocol methods (added to `ports.py` + redis + memory adapters +
`test_core_ports._Stub`), keeping `acquire/release_global_lock`:
- `acquire_provider_slot(kind, ttl) -> token | None`, `release_provider_slot(kind, token)`
  - `kind="local"` → `lock:local` token lock, cap 1.
  - `kind="cloud"` → `semaphore:cloud` ZSET, cap `CLOUD_TRANSCRIPTION_CONCURRENCY`.
    Acquire is a **single Lua script**: `ZREMRANGEBYSCORE -inf now` (reclaim expired)
    → `ZCARD < cap ?` → `ZADD now+ttl token` → return token else nil. Release =
    `ZREM token`.
- `mark_processing(job_id)` / `clear_processing(job_id)`; `mark_dead(job_id)` /
  `remove_dead(job_id)` / `dead_job_ids()`; `queue_stats() -> {queued, processing, dead}`.
- Token-locks release via atomic compare-and-del Lua (`get==token → del`).

**Testability:** `FakeRedis` (tests) gains zset ops + a `register_script` that runs the
Python equivalent of each named Lua script against its in-memory store, so semaphore
semantics (5 acquire / 6th fails / release frees / expired reclaimed) are unit-tested
without a live Redis. Production uses real `register_script` (atomic EVAL).

`run_queue_loop`: dequeue → `claim_job` (skips `next_retry_at > now`) → `resolve` →
`acquire_provider_slot(kind)`; **no slot → re-enqueue + short backoff, never fail** →
`mark_processing` → `process` → on done `clear_processing` + `release_provider_slot`
(finally). `TRANSCRIPTION_QUEUE_CONCURRENCY` (default 5) consumer threads in redis mode.

## 9. Retry / backoff / dead-letter + error classification — `app/errors.py`, worker

- `AppError` subclasses gain `error_code: str` + `retryable: bool`.
  `classify_error(exc) -> (code, retryable, retry_after_seconds | None)`.
- `DeepgramClient` maps HTTP: **429 → RATE_LIMIT (retryable, honor Retry-After)**;
  **401/403 → KEY_INVALID (terminal)**; **413 → FILE_TOO_LARGE (terminal)**.
  Google API rate-limit → retryable; model-invalid / local-config / key-required →
  terminal; unknown `Exception` → retryable until cap.
- `JobRepository.schedule_retry(job_id, now, next_retry_at, error_code, error_message)`:
  status→pending, set `next_retry_at`/`last_error_code`/`error_message`, **keep
  `attempts` and `source_file_id`**. `mark_failed` gains optional `error_code`.
- Backoff: `min(JOB_RETRY_MAX_SECONDS, JOB_RETRY_BASE_SECONDS · 2^(attempts-1))`,
  floored by `retry_after`.
- Dead-letter: terminal error **or** `attempts >= JOB_MAX_ATTEMPTS` → `mark_failed`
  (+ `last_error_code`) + `queue.mark_dead(job_id)` (observability). UI retry →
  `POST /jobs/{id}/retry`: user-scoped reset failed→pending (attempts→0, clear
  `next_retry_at`/error), `remove_dead`, enqueue.

## 10. Cost guardrails — enforce-cheap + scaffold

At job creation (watcher + run-once + check-now), per-file/per-user, using global env
default unless the user's `user_automation_settings` overrides:
- **Enforced now:** `max_file_size_mb` (Drive `size` already fetched);
  `daily_jobs_limit` (new `JobRepository.count_jobs_created_since(user_id, since)`).
- **Scaffolded** (columns + config + messages, checked only where data present):
  `monthly_cloud_minutes_limit`, `max_file_duration_minutes`
  (Drive `videoMediaMetadata.durationMillis`).
Messages (pt-BR): "Limite diário de jobs atingido.", "Arquivo excede limite
permitido.", "Provider está rate-limited. Tentaremos novamente."

## 11. Observability — admin panel

`queue.queue_stats()` + recent failed/dead jobs. Admin-only `GET /admin/queue` renders
`queue_status.html`. One structured log line per poll tick and per job outcome
(no secrets).

## 12. Web UI (Jinja2, server-rendered, no SPA/CDN)

- New `AutomationSettingsRepository` (Protocol + postgres + memory), added to **both**
  the worker `Repositories` bundle and the web `RepositoryBundle`; threaded through
  `build_repositories` + `build_memory_repositories`.
- Routes in `create_app`: `GET/POST /settings/automation`, `POST /automation/check-now`,
  `POST /jobs/{id}/retry`.
- Templates: new `automation_settings.html`, `queue_status.html`; `jobs.html` gets
  "Verificar agora" + per-failed-job "Tentar novamente"; `settings.html` + `base.html`
  nav links.

## 13. Config / env + `CLAUDE.md`

Env (all with `${VAR:-default}` defaults in `docker-compose.yml` so `docker compose
config` works with no `.env`):
`AUTO_POLL_ENABLED=true`, `AUTO_POLL_INTERVAL_SECONDS=300`,
`AUTO_POLL_MAX_USERS_PER_TICK=50`, `AUTO_POLL_MAX_FILES_PER_USER=5`,
`AUTO_POLL_LOCK_TTL_SECONDS=240`, `CLOUD_TRANSCRIPTION_CONCURRENCY=5`,
`LOCAL_TRANSCRIPTION_CONCURRENCY=1`, `TRANSCRIPTION_QUEUE_CONCURRENCY=5`,
`PROVIDER_LOCK_TTL_SECONDS=14400`, `JOB_MAX_ATTEMPTS=3`, `JOB_RETRY_BASE_SECONDS=60`,
`JOB_RETRY_MAX_SECONDS=3600`, plus global guardrail defaults
(`MAX_FILE_SIZE_MB`, `DAILY_JOBS_LIMIT`, …; 0/empty = unlimited).
Split: auto-poll/queue-threads/retry/guardrails → `WorkerSettings`; semaphore caps +
provider-lock TTL → `QueueSettings`.

`CLAUDE.md` hard-rule rewrite: "cloud providers run configurable concurrency
(default 5); local CPU runs 1; Postgres remains source of truth; Redis controls
queue/locks/semaphore" (replacing "one transcription at a time / single global lock").

## 14. Contracts preserved/extended

Keep all `JobRepository` names; add `schedule_retry`, `count_jobs_created_since`,
`count_jobs_by_status`, `reset_job_for_retry`; extend `mark_failed(..., error_code=None)`,
`list_pending_jobs(now=None)`, claim methods skip future `next_retry_at`. New
`AutomationSettingsRepository`. New queue methods (§8). Implement in **both** adapters,
update the `runtime_checkable` Protocols and `tests/test_core_ports.py::_Stub`.

## 15. Test plan (TDD; dict fakes + `FakeRedis`; never sqlite)

auto-poll creates new job; no dup of completed; no dup of pending; `lock:auto_poll`
blocks a 2nd poller; BRPOP idle timeout silent; cloud allows 5 / 6th waits; local
allows 1 / 2nd waits; 429 → `next_retry_at`; max-attempts → dead-letter; requeue-pending
on startup; Drive folder error → friendly `last_error`; retry preserves `source_file_id`;
`requeue_pending_jobs` skips backoff; semaphore expired-slot reclaim; provider
classification (local-valid user who chose deepgram → cloud); guardrail file-size +
daily-limit; `_Stub`/Protocol update; automation repo (memory + pg).

## 16. Docs

`documentation/28-auto-polling.md`, `29-redis-queue-advanced.md`,
`30-provider-concurrency.md`, `31-retries-dead-letter.md`, `32-cost-guardrails.md`
(global numbering; 20–27 belong to sibling branches). Update `README.md`,
`.env.example`, `03-environment-variables.md`, `09-redis-queue.md`,
`11-worker-flow.md`, `19-roadmap.md` (Changes API + `drive_watch_state` next steps),
`CLAUDE.md`. Final `overview/feat-automation-queue-drive-watcher.md`.

## 17. Risks & limitations

- Retry latency is bounded by the auto-poll/idle reconcile cadence (not instant).
- Cloud semaphore slot TTL = `PROVIDER_LOCK_TTL_SECONDS` (4h) → a crashed worker's
  slot is reclaimed only after TTL (safe, conservative).
- Polling lists the whole folder each tick (Changes API deferred); mitigated by
  `last_poll_at` gating + DB dedupe.
- `monthly_cloud_minutes` / duration guardrails are scaffolded, not fully metered.

## 18. Out of scope (next steps)

Drive Changes API + `drive_watch_state` (pageToken state), full minute-metering,
per-provider cost accounting, multi-worker horizontal scaling beyond the single
worker container's threads.
