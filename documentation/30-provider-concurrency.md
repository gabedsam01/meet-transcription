# Provider-Aware Concurrency

Meet Transcription runs on a CPU-bound VPS, but not every transcription competes
for the CPU. **Cloud** providers (Deepgram, and future `gemini`/`openrouter`) are
I/O-bound — mostly upload + wait on a remote API — so several can run in parallel.
**Local** CPU engines (`faster-whisper`, `whisper-cpp`) saturate the CPU, so they
must run strictly one at a time.

This page documents how the **redis-queue worker** replaces the old single global
execution lock with **per-provider concurrency**: a counting semaphore for cloud
jobs and a single lock for local jobs, chosen by the **resolved** provider. The
legacy poll mode (`QUEUE_BACKEND=none`) and the legacy `python -m app.main` CLI are
**unchanged** — they stay strictly one transcription at a time.

Grounded in `app/worker/queue_loop.py`, `app/worker/processor.py`,
`app/transcription/provider_kind.py`, `app/queue/config.py`, and the queue adapters
in `app/queue/`.

For the queue keys and the original global lock, see
[Redis Queue and Lock](09-redis-queue.md); for the full worker pipeline, see
[Worker Flow](11-worker-flow.md).

## From a single global lock to per-provider slots

The original model (still documented in `09-redis-queue.md`) used one
`transcription:global_lock` so **exactly one** transcription ran across the whole
deployment. That serialized cloud jobs unnecessarily.

In redis-queue mode the worker now acquires a **provider slot** instead of the
global lock. Two slot kinds exist (`app/queue/redis_queue.py`):

| Kind    | Redis key          | Type   | Capacity                          |
| ------- | ------------------ | ------ | --------------------------------- |
| `local` | `lock:local`       | string | 1 (a single token lock, `SET NX EX`) |
| `cloud` | `semaphore:cloud`  | zset   | `CLOUD_TRANSCRIPTION_CONCURRENCY` slots scored by expiry |

The cloud semaphore is a ZSET of slot tokens scored by their expiry; acquire is a
**single atomic Lua script** (reclaim expired tokens → check `ZCARD < capacity` →
`ZADD` the new token, else return nil) so five threads can never grab the same
slot. The local lock is a token string with TTL, released by an atomic
compare-and-del. The legacy `acquire_global_lock` / `release_global_lock` methods
are **retained** on the queue Protocol for back-compat but are not used by the
per-provider loop.

## Classification is by the *resolved* provider

The concurrency kind is decided by `classify_provider_kind(name)`
(`app/transcription/provider_kind.py`), **not** by whether a local engine happens
to be valid:

```python
CLOUD_PROVIDERS = frozenset({"deepgram", "gemini", "openrouter"})
LOCAL_PROVIDERS = frozenset({"faster-whisper", "whisper-cpp"})

def classify_provider_kind(name):
    key = (name or "").strip().lower()
    if key in LOCAL_PROVIDERS:
        return "local"
    return "cloud"          # unknown/blank defaults to cloud (safe to overcommit)
```

`JobProcessor.resolve(job)` (`app/worker/processor.py`) loads the user's settings,
Google token, and Deepgram-key presence, runs the product provider rule, and
returns a frozen `ResolvedProvider(provider, name, kind, status, settings, token)`.
The `kind` is derived from the resolved provider's identity — so a user who has a
valid local engine but **chose Deepgram** produces a **cloud** job, not a local
one. Unknown names default to `cloud`: the safe side to overcommit, and it never
serializes a cloud provider behind the single local lock. A terminal resolution
error (no provider available, key invalid) is **not** classified — the loop
dead-letters it without taking any slot.

## The queue loop with provider slots

Each iteration of `run_queue_loop` (`app/worker/queue_loop.py`):

1. `job_id = queue.dequeue(timeout)`. `None` → run the idle reconcile
   (`requeue_pending_jobs`) and continue.
2. `get_job(job_id)`; if it is missing or no longer `pending`, drop it (the
   reconciler re-enqueues a genuinely-pending job when due).
3. `resolved = _safe_resolve(proc, job)`. If resolution raises a terminal error,
   `claim_job` the id and call `process()` (which re-resolves, fails, and
   dead-letters it — **no slot, no retry**).
4. `token = queue.acquire_provider_slot(resolved.kind, ttl)` where `ttl` is
   `PROVIDER_LOCK_TTL_SECONDS`.
   - **No slot free → `queue.requeue(job_id)` (back to the tail), short backoff,
     continue. The job is never failed.**
5. With the slot held (in a `finally` that always releases it): `claim_job`
   (atomic `pending → processing`), `mark_processing`, `process(job, resolved)`
   (no double-resolve), then `clear_processing` and `release_provider_slot`.

```python
token = queue.acquire_provider_slot(resolved.kind, ttl)
if token is None:
    # No slot for this provider kind right now — try later, never fail.
    queue.requeue(job_id)
    contention()
    continue
try:
    claimed = container.repositories.jobs.claim_job(job_id, worker_id, now())
    ...
    queue.mark_processing(claimed.id)
    try:
        proc.process(claimed, resolved)
    finally:
        queue.clear_processing(claimed.id)
finally:
    queue.release_provider_slot(resolved.kind, token)
```

So contention is resolved by **requeueing with a delay**, never by failing a job.
At most `CLOUD_TRANSCRIPTION_CONCURRENCY` cloud transcriptions and one local
transcription run at any instant, regardless of how many consumer threads exist.

## Consumer threads and slot reclaim

In redis-queue mode the worker fans out `TRANSCRIPTION_QUEUE_CONCURRENCY` threads
(default 5), each running `run_queue_loop` with a distinct `worker_id`
(`app/worker/main.py` → `container.settings.queue_concurrency`). These threads
provide the parallelism; the **slots** bound it per provider kind. (The legacy
poll loop instead uses `WORKER_CONCURRENCY`.)

Every slot carries a TTL of `PROVIDER_LOCK_TTL_SECONDS` (default `14400` = 4 h). If
a worker crashes mid-job and never releases its slot, the TTL lets Redis reclaim
it: the local lock simply expires, and the cloud acquire script removes expired
tokens (`ZREMRANGEBYSCORE -inf now`) before counting. Reclaim is conservative — a
crashed worker's slot frees only after its TTL — but it guarantees the system is
never permanently wedged.

## What stays strictly one-at-a-time

Per-provider concurrency exists **only** in redis-queue mode. Two paths are
deliberately untouched and still process strictly one transcription at a time:

- **`QUEUE_BACKEND=none` (legacy poll loop).** `container.queue is None`, so the
  worker selects `run_worker_loop` and claims the next pending job directly with
  `claim_next_pending_job`. No semaphore, no provider slot.
- **The legacy CLI `python -m app.main`** (`--once` / `--watch` / `--reprocess`).
  The env-driven CLI that stores state in `data/processed_files.json` is a
  supported deployment and is completely unaffected by this feature.

## Environment variables

All have `${VAR:-default}` defaults in `docker-compose.yml`, so `docker compose
config` works with no `.env`. The two semaphore caps and the slot TTL live on
`QueueSettings` (`app/queue/config.py`); the consumer-thread count lives on
`WorkerSettings` (`app/worker/config.py`).

| Variable                          | Default | Meaning                                                                  |
| --------------------------------- | ------- | ------------------------------------------------------------------------ |
| `CLOUD_TRANSCRIPTION_CONCURRENCY` | `5`     | Capacity of the `semaphore:cloud` zset — parallel cloud (I/O-bound) jobs. |
| `LOCAL_TRANSCRIPTION_CONCURRENCY` | `1`     | Capacity of the local CPU slot. Modeled as the single `lock:local`.      |
| `TRANSCRIPTION_QUEUE_CONCURRENCY` | `5`     | Consumer threads running `run_queue_loop` (redis mode only).             |
| `PROVIDER_LOCK_TTL_SECONDS`       | `14400` | TTL (s) on every provider slot; reclaims a crashed worker's slot.        |

All four must parse as integers `> 0` (`_positive_int`). The cloud capacity is
floored at 1 in the Redis adapter (`max(1, cloud_concurrency)`).

> The provider rule itself (Deepgram vs. local engine selection) is unchanged — see
> [Local Transcription](06-local-transcription.md). This page only covers how the
> resolved provider's *kind* drives concurrency.
