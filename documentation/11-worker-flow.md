# Worker Flow

> **Automation additions.** Besides consuming the queue, the worker now runs an
> in-process **auto-poll** thread ([28](28-auto-polling.md)), processes jobs under
> **provider-aware concurrency** ([30](30-provider-concurrency.md)), and handles
> **retries / dead-letter** ([31](31-retries-dead-letter.md)). This page describes
> the core single-job flow those build on.

The **worker** is the out-of-band job processor of Meet Transcription. It runs as a
dedicated container (`command: python -m app.worker.main`) sharing the same image as
the web service, and it is the **only** place where downloading, transcription, and
Drive uploads happen. The web service never transcribes in-request — it only validates
input, creates a `pending` job, and enqueues its id (see [Web Routes](12-web-ui.md)).

This document walks through every step the worker performs, in execution order, with
the exact log lines emitted at each step. The relevant source files are:

| File | Responsibility |
| --- | --- |
| `app/worker/main.py` | Entry point: startup recovery, mode selection, thread fan-out, signals |
| `app/worker/container.py` | Dependency wiring (`WorkerContainer`, `build_container`) |
| `app/worker/queue_loop.py` | Redis-queue loop (`run_queue_loop`) — global lock + `claim_job` |
| `app/worker/loop.py` | Legacy poll loop (`run_worker_loop`) — `claim_next_pending_job` |
| `app/worker/processor.py` | The per-job pipeline (`JobProcessor.process`) |

See [Architecture](01-architecture.md) for how the worker fits into the 5-service
compose stack, [Queue and Locking](09-redis-queue.md) for the Redis contract, and
[Transcription Providers](06-local-transcription.md) for the provider rule.

---

## High-level sequence

```
python -m app.worker.main
  └─ setup_logging()
  └─ build_container()                     # WorkerSettings + repositories + queue + providers
  └─ install SIGINT/SIGTERM handlers       # set stop_event only (signal-safe)
  └─ run(container, stop_event)
       1. recover_stale_jobs()             # reset stale 'processing' jobs to 'failed'
       2. if container.queue is not None:  # QUEUE_BACKEND=redis|memory
            requeue_pending_jobs()         # reconcile Postgres pending -> queue
            loop = run_queue_loop
          else:                            # QUEUE_BACKEND=none
            loop = run_worker_loop         # legacy poll
       3. start WORKER_CONCURRENCY daemon threads, each running `loop`
       4. join() all threads (blocks until stop_event)
```

Each loop iteration ultimately calls `JobProcessor.process(job)`:

```
dequeue (queue mode)        → acquire global lock → claim_job → process → release lock
claim_next_pending_job (poll mode) → process
process:
  check settings/token/source_file_id
    → resolve provider
    → download MP4 from Drive (into per-job tmp dir)
    → provider.transcribe
    → optional Drive .txt upload
    → transcripts.create
    → mark_completed
  on any failure → mark_failed(user_message)
  always → clean up the per-job tmp dir
```

---

## Step 1 — Startup and dependency wiring

`main()` (`app/worker/main.py`) runs first:

```python
def main() -> int:
    setup_logging()
    container = build_container()
    stop_event = threading.Event()
    ...
```

`build_container()` (`app/worker/container.py`) assembles a `WorkerContainer` from the
environment:

- `WorkerSettings.from_env()` — reads `WORKER_REPOSITORY_BACKEND`,
  `WORKER_POLL_INTERVAL_SECONDS`, `WORKER_CONCURRENCY` (default `1`),
  `STALE_JOB_TIMEOUT_MINUTES`, `TMP_DIR`, plus Deepgram defaults.
- `build_repositories(worker_settings.repository_backend)` — the `postgres` adapter
  (`app/repositories/postgres.py`) in production, or the dict-backed `memory` fake
  (`app/repositories/memory.py`) for dev/tests. **There is no SQLite path.**
- `TranscriptionConfig.from_env()` — the `LOCAL_TRANSCRIPTION_*` settings.
- `QueueSettings.from_env()` + `build_queue(...)` — reads `QUEUE_BACKEND`
  (`redis|memory|none`), `REDIS_URL`, `QUEUE_NAME`, and
  `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS`. When `QUEUE_BACKEND=none`, `build_queue`
  returns `None`, which selects the legacy poll loop later.
- Factory callables: `build_drive_client`, `build_deepgram_client`,
  `credentials_from_token`, and `build_local_provider`.

The `queue_lock_ttl` defaults to `14400` seconds (4 hours), matching
`TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS`.

### Signal handling

`main()` installs handlers for `SIGINT` and `SIGTERM` that **only** set
`stop_event`:

```python
def _handle_signal(_signum, _frame):
    # Keep the handler async-signal-safe: only set the event. Logging acquires a
    # lock and is not safe to call from a signal handler.
    stop_event.set()
```

This gives a clean shutdown: every loop tests `while not stop_event.is_set()` and the
default sleeps return as soon as the event is set.

**Log emitted at startup:**

```
Worker starting backend=<postgres|memory> concurrency=<N>
```

On exit (after all threads join):

```
Worker stopped
```

---

## Step 2 — Stale-job recovery (`recover_stale_jobs`)

Before consuming any work, `run()` recovers jobs that were left in `processing` by a
previous crashed/killed worker. This is the first thing it does:

```python
def run(container, stop_event):
    recover_stale_jobs(container, datetime.now(timezone.utc))
    ...
```

`recover_stale_jobs` computes a cutoff and asks the repository to reset anything stuck:

```python
def recover_stale_jobs(container, now):
    stale_before = now - timedelta(minutes=container.settings.stale_job_timeout_minutes)
    reset = container.repositories.jobs.reset_stale_processing_jobs(stale_before, now)
    if reset:
        LOGGER.warning("Recovered %s stale processing job(s) to failed", len(reset))
    return len(reset)
```

- `stale_job_timeout_minutes` comes from `STALE_JOB_TIMEOUT_MINUTES`.
- `reset_stale_processing_jobs(stale_before, now)` is part of the
  `JobRepository` contract (`app/core/ports.py`); the Postgres adapter flips any
  `processing` job started before `stale_before` to `failed`.

**Log emitted (only when something was recovered):**

```
Recovered <K> stale processing job(s) to failed
```

This runs once per worker start, before either loop begins.

---

## Step 3 — Queue vs poll mode selection

After recovery, `run()` branches on whether a queue was wired:

```python
if container.queue is not None:
    enqueued = requeue_pending_jobs(container.repositories, container.queue)
    LOGGER.info("Queue mode: reconciled %s pending job(s) at startup", enqueued)
    loop = run_queue_loop
else:
    loop = run_worker_loop
```

| `QUEUE_BACKEND` | `container.queue` | Loop selected | Claim mechanism |
| --- | --- | --- | --- |
| `redis` (compose default) | Redis queue | `run_queue_loop` | global lock + `claim_job(job_id)` |
| `memory` | in-process queue | `run_queue_loop` | global lock + `claim_job(job_id)` |
| `none` (code default) | `None` | `run_worker_loop` | `claim_next_pending_job` |

> Note: the **code** default for `QUEUE_BACKEND` is `none` (legacy poll), but
> `docker-compose.yml` sets it to `redis`. The production stack therefore runs the
> queue loop.

### Queue reconcile at startup

In queue mode, `requeue_pending_jobs(container.repositories, container.queue)` re-enqueues
**every** Postgres `pending` job back onto Redis. This self-heals the case where the
queue was lost (Redis restart, flush) while Postgres still holds pending work. It is safe
because the `transcription:queued` dedupe set prevents duplicate ids, and the eventual
`claim_job` is the final atomic dedupe in Postgres.

**Log emitted (queue mode only):**

```
Queue mode: reconciled <M> pending job(s) at startup
```

### Thread fan-out

Regardless of mode, `run()` starts `WORKER_CONCURRENCY` daemon threads, each running the
selected loop with a distinct `worker_id`, then joins them:

```python
for i in range(container.settings.concurrency):
    worker_id = f"worker-{i + 1}"
    thread = threading.Thread(target=loop, args=(container, stop_event, worker_id),
                              name=worker_id, daemon=True)
    thread.start()
```

Worker ids are `worker-1`, `worker-2`, … and appear in the log lines below. Even with
concurrency > 1, the **global lock** in queue mode guarantees only one transcription runs
at a time across all threads and processes (CPU-bound VPS protection).

---

## Step 4a — Queue loop (`run_queue_loop`)

This is the production path. Each iteration (`app/worker/queue_loop.py`):

1. **Dequeue.** `job_id = queue.dequeue(timeout)` does a blocking `BRPOP` on
   `transcription:queue` for up to `timeout` seconds (default
   `WORKER_POLL_INTERVAL_SECONDS`).
   - If `job_id is None` (nothing dequeued), the loop calls the **idle** callback,
     which defaults to `requeue_pending_jobs(...)` — re-enqueuing any Postgres pending
     jobs the queue may have dropped — then continues. (No log line on idle.)
   - Otherwise it logs:
     ```
     Queue job received: job_id=<id> worker=<worker-N>
     ```

2. **Acquire the global lock.** `token = queue.acquire_global_lock(container.queue_lock_ttl)`
   does a Redis `SET transcription:global_lock <token> NX EX <ttl>`.
   - If `token is None`, another worker holds the single execution lock. The job is put
     **back** on the queue (`queue.requeue(job_id)`), the **contention** callback runs
     (defaults to `stop_event.wait(1)` — a 1-second backoff), and the loop continues.
     (No log line on contention.)

3. **Claim the job in Postgres.** Inside a `try/finally` that guarantees lock release:
   ```python
   job = container.repositories.jobs.claim_job(job_id, worker_id, now())
   if job is None:
       LOGGER.info("Queued job_id=%s is no longer pending; skipping", job_id)
       continue
   LOGGER.info("Claimed job_id=%s worker=%s", job.id, worker_id)
   ```
   `claim_job` atomically transitions `pending -> processing` for that specific id; if it
   returns `None`, the job was already claimed/completed elsewhere (the final dedupe
   defense), so it is skipped.

4. **Process.** `proc.process(job)` runs the pipeline (Step 5). A single job failing
   never kills the loop:
   ```python
   try:
       proc.process(job)
   except Exception:  # a single job must never kill the loop.
       LOGGER.exception("Unhandled error processing job_id=%s", job_id)
   ```

5. **Release the lock.** The `finally` block always runs
   `queue.release_global_lock(token)`, so the lock is freed even on error.

### Transient-error resilience (queue loop)

The entire iteration body is wrapped in a broad `try/except` so that transient Redis or
Postgres failures (dequeue, reconcile, lock, claim) **never** kill the daemon thread:

```python
except Exception:  # survive transient queue/database errors.
    LOGGER.exception("Queue worker iteration failed worker=%s", worker_id)
    error_backoff()
```

The default `error_backoff` waits `min(timeout or 1, 5)` seconds via
`stop_event.wait(...)`, so it backs off but still wakes immediately on shutdown.

**Queue-loop log lines, in order:**

| When | Level | Message |
| --- | --- | --- |
| job popped from queue | INFO | `Queue job received: job_id=<id> worker=<worker-N>` |
| claimed but no longer pending | INFO | `Queued job_id=<id> is no longer pending; skipping` |
| claim succeeded | INFO | `Claimed job_id=<id> worker=<worker-N>` |
| job raised an exception | ERROR (with traceback) | `Unhandled error processing job_id=<id>` |
| whole iteration failed (transient) | ERROR (with traceback) | `Queue worker iteration failed worker=<worker-N>` |

---

## Step 4b — Poll loop (`run_worker_loop`, `QUEUE_BACKEND=none`)

The legacy poll loop (`app/worker/loop.py`) is selected when no queue is configured. It
claims directly from Postgres without Redis or a global lock:

```python
while not stop_event.is_set():
    job = container.repositories.jobs.claim_next_pending_job(worker_id, now())
    if job is None:
        sleeper(container.settings.poll_interval_seconds)
        continue
    LOGGER.info("Claimed job_id=%s worker=%s", job.id, worker_id)
    try:
        proc.process(job)
    except Exception:  # a single job must never kill the worker loop.
        LOGGER.exception("Unhandled error processing job_id=%s worker=%s", job.id, worker_id)
```

- `claim_next_pending_job(worker_id, now)` atomically picks and claims the oldest
  `pending` job (`pending -> processing`).
- When there is nothing to do, it sleeps `WORKER_POLL_INTERVAL_SECONDS` via
  `stop_event.wait`, which returns immediately on shutdown.
- A failing job is logged with a traceback but never stops the loop.

**Poll-loop log lines:**

| When | Level | Message |
| --- | --- | --- |
| claim succeeded | INFO | `Claimed job_id=<id> worker=<worker-N>` |
| job raised an exception | ERROR (with traceback) | `Unhandled error processing job_id=<id> worker=<worker-N>` |

> Unlike the queue loop, the poll loop does **not** wrap the claim call in a broad
> backoff handler; a hard repository failure on `claim_next_pending_job` would propagate.
> Use queue mode in production.

---

## Step 5 — The per-job pipeline (`JobProcessor.process`)

`JobProcessor.process(job)` (`app/worker/processor.py`) is the heart of the worker. It is
written so that **every job reaches a terminal state** (`completed` or `failed`) and the
per-job scratch directory is **always** cleaned up.

### 5.0 — Tmp isolation

The first thing `process` computes is an isolated scratch directory, unique per job:

```python
job_dir = Path(self.container.settings.tmp_dir) / "jobs" / str(job.id)
```

So a job's working files live under `TMP_DIR/jobs/<job_id>/`. With `TMP_DIR=/app/tmp` (the
default, compose mount `./tmp:/app/tmp`), that is e.g. `/app/tmp/jobs/42/`. This isolation means concurrent jobs
never collide, and cleanup of one job never touches another.

### 5.1 — Precondition checks

The job is only processable if its owner's settings, Google token, and source file id are
present. Each missing piece raises a typed `AppError` with a friendly `user_message`:

```python
settings = repos.settings.get(job.user_id)
if settings is None:
    raise DriveFolderMissingError("User settings are required before transcription")
token = repos.google_tokens.get(job.user_id)
if token is None:
    raise GoogleTokenMissingError("Google token is required before transcription")
if not job.source_file_id:
    raise DriveFolderMissingError("Job has no source_file_id to download")
```

### 5.2 — Resolve the transcription provider

`_resolve_provider(settings)` applies the product rule via
`app/transcription/factory.py:resolve_provider` (see
[Transcription Providers](06-local-transcription.md)):

- `LOCAL_TRANSCRIPTION_ENABLED=false` → Deepgram; the per-user `deepgram_api_key` is
  required (its absence raises a clear, Deepgram-mentioning error).
- enabled + valid local engine → local provider; **no Deepgram key required**.
- enabled + invalid → Deepgram required (no silent fallback).

The chosen provider is labeled for logs: `status.summary` (e.g. the local
`engine model compute/quant`) when local is valid, otherwise `"deepgram"`.

**Log emitted when the provider is chosen:**

```
Transcription started: job_id=<id> user_id=<uid> provider=<label>
```

### 5.3 — Build the Drive client and download the MP4

```python
credentials = self.container.credentials_from_token(token)
drive = self.container.build_drive_client(
    credentials,
    settings.source_drive_folder_id,
    settings.destination_drive_folder_id,
)

job_dir.mkdir(parents=True, exist_ok=True)
safe_base = sanitize_filename(job.source_file_name or job.source_file_id)
video_path = job_dir / f"{safe_base}.mp4"
drive.download_by_id(job.source_file_id, video_path)
```

- Credentials are reconstructed from the **encrypted** Google token via
  `credentials_from_token`.
- The job directory is created lazily (`mkdir(parents=True, exist_ok=True)`).
- `sanitize_filename` keeps the on-disk name safe; the MP4 lands inside the per-job dir.

### 5.4 — Transcribe

```python
result = provider.transcribe(
    video_path,
    original_name=job.source_file_name or "",
    file_id=job.source_file_id,
)
transcript_text = result.text
```

`result.text` is the human-readable `.txt`. `result.payload` is the normalized JSON
schema (`provider`, `engine`, `model`, `language`, `text`, `segments`, `words`,
`utterances`, `raw`) — see [Normalized Transcript Schema](10-postgres-and-migrations.md). For
whisper.cpp, scratch under `<job_dir>/whispercpp` is written and cleaned by the provider
itself.

### 5.5 — Optional Drive TXT upload

A backup copy of the transcript is uploaded **only** when the user opted in and a
destination folder is configured:

```python
transcript_drive_file_id = None
if settings.save_copy_to_drive and settings.destination_drive_folder_id:
    transcript_filename = f"{safe_base}_Transcricao.txt"
    transcript_path = job_dir / transcript_filename
    transcript_path.write_text(transcript_text, encoding="utf-8")
    transcript_drive_file_id = drive.upload_text_file(transcript_path, transcript_filename)
```

If either condition is false, `transcript_drive_file_id` stays `None` and nothing is
uploaded — PostgreSQL remains the source of truth and the UI still serves Download TXT.

### 5.6 — Persist the transcript and mark completed

```python
repos.transcripts.create(
    job_id=job.id, user_id=job.user_id, text=transcript_text,
    json_payload=result.payload, drive_file_id=transcript_drive_file_id,
    now=self._now(),
)
repos.jobs.mark_completed(
    job.id, self._now(), transcript_drive_file_id=transcript_drive_file_id
)
```

The transcript row holds both the `.txt` text and the normalized JSON; the job is flipped
to `completed` and records the optional Drive file id.

**Log emitted on success:**

```
Transcription completed: job_id=<id> provider=<label> duration_seconds=<elapsed>
```

`duration_seconds` is measured from a `time.monotonic()` stamp taken at the top of
`process`.

### 5.7 — Failure handling (one job never kills the loop)

Any exception in the pipeline is caught so the job always reaches a terminal state.
`AppError` subclasses carry a secret-free `user_message`; anything else falls back to
`str(exc)`. The **full traceback goes to logs only** — never to the stored job error:

```python
except Exception as exc:  # a job must always reach a terminal state.
    user_message = exc.user_message if isinstance(exc, AppError) else str(exc)
    LOGGER.exception("Transcription failed: job_id=%s reason=%s", job.id, exc)
    repos.jobs.mark_failed(job.id, user_message, self._now())
```

The friendly `user_message` is stored in `transcription_jobs.error_message` and shown in
the UI. See [Errors](15-troubleshooting.md) for the full `AppError` hierarchy
(`TranscriptionProviderError`, `LocalTranscriptionUnavailableError`,
`DeepgramKeyRequiredError`, `ModelNotFoundError`, `WhisperCppBinaryNotFoundError`,
`GoogleTokenMissingError`, `DriveFolderMissingError`, …).

**Log emitted on failure:**

```
Transcription failed: job_id=<id> reason=<exc>   (with full traceback)
```

### 5.8 — Cleanup (always)

A `finally` block guarantees the per-job scratch directory is removed whether the job
succeeded or failed:

```python
finally:
    _cleanup_job_dir(job_dir)
```

```python
def _cleanup_job_dir(job_dir):
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
```

`shutil.rmtree(..., ignore_errors=True)` removes the directory; only an unexpected
`OSError` would log a warning:

```
Could not remove job workspace <path>: <error>
```

---

## Complete log reference (by step)

| Step | Source | Level | Log message |
| --- | --- | --- | --- |
| Startup | `main` | INFO | `Worker starting backend=<backend> concurrency=<N>` |
| Stale recovery | `recover_stale_jobs` | WARNING | `Recovered <K> stale processing job(s) to failed` |
| Queue reconcile | `run` | INFO | `Queue mode: reconciled <M> pending job(s) at startup` |
| Dequeue | `run_queue_loop` | INFO | `Queue job received: job_id=<id> worker=<worker-N>` |
| Claim (queue) | `run_queue_loop` | INFO | `Claimed job_id=<id> worker=<worker-N>` |
| Claim no-op | `run_queue_loop` | INFO | `Queued job_id=<id> is no longer pending; skipping` |
| Claim (poll) | `run_worker_loop` | INFO | `Claimed job_id=<id> worker=<worker-N>` |
| Provider chosen | `JobProcessor.process` | INFO | `Transcription started: job_id=<id> user_id=<uid> provider=<label>` |
| Success | `JobProcessor.process` | INFO | `Transcription completed: job_id=<id> provider=<label> duration_seconds=<s>` |
| Job failure | `JobProcessor.process` | ERROR | `Transcription failed: job_id=<id> reason=<exc>` (+ traceback) |
| Loop-level job error | `run_queue_loop` / `run_worker_loop` | ERROR | `Unhandled error processing job_id=<id> ...` (+ traceback) |
| Transient iteration error | `run_queue_loop` | ERROR | `Queue worker iteration failed worker=<worker-N>` (+ traceback) |
| Cleanup failure | `_cleanup_job_dir` | WARNING | `Could not remove job workspace <path>: <error>` |
| Shutdown | `main` | INFO | `Worker stopped` |

---

## Relevant environment variables

| Variable | Default | Effect on the worker |
| --- | --- | --- |
| `WORKER_REPOSITORY_BACKEND` | `postgres` | `postgres` adapter or `memory` fake (dev/tests only) |
| `WORKER_CONCURRENCY` | `1` | Number of daemon loop threads |
| `WORKER_POLL_INTERVAL_SECONDS` | — | Dequeue/poll wait and error backoff base |
| `STALE_JOB_TIMEOUT_MINUTES` | — | Cutoff for `reset_stale_processing_jobs` at startup |
| `TMP_DIR` | — | Root of per-job scratch dirs (`TMP_DIR/jobs/<job_id>/`) |
| `QUEUE_BACKEND` | `none` (code) / `redis` (compose) | Selects `run_queue_loop` vs `run_worker_loop` |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection for queue + lock |
| `QUEUE_NAME` | `transcription` | Base name for the Redis keys |
| `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS` | `14400` | TTL of `transcription:global_lock` |
| `LOCAL_TRANSCRIPTION_*` | see providers doc | Provider resolution (`TranscriptionConfig.from_env`) |

> The web/worker deployment does **not** read the global `DEEPGRAM_API_KEY`. Deepgram
> keys are per-user and encrypted at rest in Postgres
> (`deepgram_credentials.encrypted_api_key`).

---

## Running and observing the worker

Start the full stack (worker included):

```bash
cp .env.example .env        # fill in secrets
docker compose up -d
docker compose logs -f worker
```

Run only the worker locally (after `migrate` has applied the schema):

```bash
python -m app.worker.main
```

Graceful stop (sends `SIGTERM`, which sets `stop_event`):

```bash
docker compose stop worker
```

For the queue keys, dedupe set, and global lock semantics referenced above, see
[Queue and Locking](09-redis-queue.md).
