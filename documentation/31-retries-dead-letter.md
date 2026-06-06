# Retries, Backoff and Dead-Letter

When a transcription job fails, the worker decides — based on **why** it failed —
whether to retry it later or give up. Retries are **scheduled in PostgreSQL**
(`next_retry_at`), never in Redis, so a full Redis wipe never loses a job's place
in the retry timeline. Redis only mirrors the dead-letter set for observability.

This page is grounded in `app/errors.py` (`classify_error` + the typed
`AppError`s), `app/deepgram_client.py` (HTTP → error mapping),
`app/worker/processor.py` (`_handle_failure` + `_backoff`),
`app/repositories/postgres.py` (`schedule_retry` / `mark_failed` /
`reset_job_for_retry`), and `app/database/models.py`
(`next_retry_at` / `last_error_code` on `transcription_jobs`).

See [Worker Flow](11-worker-flow.md) for the full per-job pipeline and
[Redis Queue and Lock](09-redis-queue.md) for the queue contract.

## Error classification — retryable vs terminal

Every mapped failure is an `AppError` carrying `error_code` (a stable machine
code stored on the job as `last_error_code`) and `retryable`.
`classify_error(exc)` returns `(error_code, retryable, retry_after_seconds)`:

```python
def classify_error(exc):
    if isinstance(exc, AppError):
        return exc.error_code, exc.retryable, getattr(exc, "retry_after_seconds", None)
    return "UNEXPECTED", True, None
```

`DeepgramClient.transcribe` maps the HTTP statuses the policy cares about to
typed errors:

| HTTP   | Error                       | `error_code`     | `retryable` | Notes                          |
| ------ | --------------------------- | ---------------- | ----------- | ------------------------------ |
| 429    | `DeepgramRateLimitError`    | `RATE_LIMIT`     | **True**    | honors `Retry-After` (seconds) |
| 401/403| `ProviderKeyInvalidError`   | `KEY_INVALID`    | False       | terminal — bad/invalid key     |
| 413    | `FileTooLargeError`         | `FILE_TOO_LARGE` | False       | terminal — file over the limit |

Other terminal `AppError`s (all `retryable = False`): `CONFIG` from
`LocalTranscriptionUnavailableError`, `DeepgramKeyRequiredError`,
`LocalTranscriptionConfigError` / `ModelNotFoundError` /
`WhisperCppBinaryNotFoundError` (invalid local model/binary).

**Anything not mapped** — a bare `Exception`, a transient Drive/network blip, a
Google API rate-limit, an unexpected provider error — falls through to
`("UNEXPECTED", True, None)` and is **retryable** up to the attempt cap. The bias
is deliberate: a transient blip must never permanently fail a job before the
dead-letter cap, while a known-terminal cause (invalid key, oversized file,
broken config) is never retried.

## Postgres-gated retry scheduling

`_handle_failure` runs inside the pipeline's `except` block. `attempts` was
already incremented when the job was claimed (`pending → processing`), so the
**first** failure has `attempts == 1`:

```python
code, retryable, retry_after = classify_error(exc)
user_message = exc.user_message if isinstance(exc, AppError) else str(exc)

if retryable and job.attempts < settings.job_max_attempts:
    delay = _backoff(job.attempts, settings.job_retry_base_seconds,
                     settings.job_retry_max_seconds, retry_after)
    repos.jobs.schedule_retry(
        job.id, self._now(),
        next_retry_at=self._now() + timedelta(seconds=delay),
        error_code=code, error_message=user_message,
    )
    return
```

`schedule_retry` flips the row **back to `pending`**, stamps `next_retry_at`,
`last_error_code` and the friendly `error_message` — and **keeps `attempts` and
`source_file_id` untouched** so the next attempt counts up and still knows which
Drive file to download:

```python
job.status = "pending"
job.next_retry_at = next_retry_at
job.last_error_code = error_code
job.error_message = error_message
```

A re-`pending` job is invisible to the worker until its gate passes. Both claim
queries and the pending listing apply the `_due_predicate` —
`next_retry_at IS NULL OR next_retry_at <= now` — so a job still in backoff is
**skipped** by `claim_next_pending_job`, `claim_job`, and
`list_pending_jobs(now)`. Reconciliation (`requeue_pending_jobs`) re-enqueues
only due jobs, so a job in backoff stays out of Redis until it is eligible.

The `transcription_jobs(status, next_retry_at)` index
(`ix_transcription_jobs_status_next_retry`) backs that sweep.

## Backoff formula

`_backoff` is exponential, capped, and floored by any provider `Retry-After`:

```python
delay = min(maximum, base * (2 ** max(0, attempts - 1)))
if retry_after:
    delay = max(delay, int(retry_after))
return delay
```

With the defaults (`base = 60s`, `max = 3600s`): attempt 1 → 60s, attempt 2 →
120s, then 240s, 480s … capped at one hour. A Deepgram 429's `Retry-After`
raises the floor — the wait is `max(exponential_delay, retry_after)`, so the
worker never hammers a provider before it said it's ready.

## Dead-letter

A job dead-letters when it is **terminal** (`retryable == False`) **or** has
exhausted its attempts (`attempts >= JOB_MAX_ATTEMPTS`). `_handle_failure` then
marks it failed and records it in the Redis dead-letter set:

```python
repos.jobs.mark_failed(job.id, user_message, self._now(), error_code=code)
queue = self.container.queue
if queue is not None:
    try:
        queue.mark_dead(job.id)
    except Exception:  # DLQ bookkeeping must not crash the loop.
        LOGGER.warning("Could not add job_id=%s to the dead-letter set", job.id)
```

`mark_failed` sets `status="failed"`, stores the friendly `error_message`, and
`last_error_code`. The job stays authoritative in Postgres; `mark_dead` adds the
id to the `transcription:dead` set purely for the admin queue panel
(see [Redis Queue Advanced](29-redis-queue-advanced.md)). If Redis is down the
DLQ write is logged and skipped — it never crashes the worker loop.

## Manual retry — the "Tentar novamente" button

A failed job exposes a per-job **"Tentar novamente"** action on the jobs page,
posting to `POST /jobs/{job_id}/retry` (`app/web/main.py`). The route is
owner-scoped (another user's job 404s) and only acts on `status == "failed"`:

```python
worker_repos.jobs.reset_job_for_retry(job_id, _utc_now())
if app.state.queue is not None:
    try:
        app.state.queue.remove_dead(job_id)
        app.state.queue.enqueue(job_id)
    except Exception:  # reconciler re-enqueues if Redis is down.
        logging.exception("Could not re-enqueue retried job_id=%s", job_id)
```

`reset_job_for_retry` is a **full reset**, unlike the automatic `schedule_retry`:
`status="pending"`, `attempts=0`, and `next_retry_at` / `error_message` /
`last_error_code` / `started_at` / `processed_at` all cleared. The id is removed
from the dead-letter set (`remove_dead`) and re-enqueued. If Redis is
unavailable the job still becomes `pending` in Postgres and is picked up by the
worker's startup/idle reconciliation — the enqueue is best-effort and never 500s.

## Configuration

`WorkerSettings.from_env` (`app/worker/config.py`) reads the retry policy. All
have `${VAR:-default}` defaults in `docker-compose.yml`:

| Variable                | Default | Meaning                                                              |
| ----------------------- | ------- | ------------------------------------------------------------------- |
| `JOB_MAX_ATTEMPTS`      | `3`     | Total attempts before a retryable failure dead-letters.             |
| `JOB_RETRY_BASE_SECONDS`| `60`    | First-retry delay; doubles each attempt (`base · 2^(attempts-1)`).  |
| `JOB_RETRY_MAX_SECONDS` | `3600`  | Upper cap on the backoff delay (1 hour).                            |

> Retry latency is bounded by how quickly a due job is re-claimed — the queue's
> idle reconcile / auto-poll cadence, not an instant timer. A `next_retry_at` in
> the past simply makes the job eligible on the next sweep.
