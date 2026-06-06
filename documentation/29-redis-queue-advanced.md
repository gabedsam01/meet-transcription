# Advanced Redis Queue, Locks and Semaphore

The base queue page ([Redis Queue and Lock](09-redis-queue.md)) covers the FIFO
list, the dedupe set, and the single global execution lock. This page documents
the **advanced** keys the automation layer adds: per-provider concurrency
(local lock + cloud semaphore), generic named locks, the observability sets
(`processing` / `dead`), and the backoff-aware `requeue_pending_jobs`.

The golden rule does not change: **PostgreSQL is the single source of truth.**
Redis is only the wake-up signal, the locks, and the semaphore. Everything Redis
holds is derivable from Postgres, so a full Redis wipe loses no work — the worker
rebuilds the queue from `pending` jobs (`requeue_pending_jobs`).

Grounded in `app/queue/ports.py`, `app/queue/redis_queue.py`,
`app/queue/memory_queue.py`, `app/queue/__init__.py`, and `app/queue/config.py`.

## Key layout

`queue_name` defaults to `transcription` (env `QUEUE_NAME`). The provider
locks and semaphore use **fixed** key names (not prefixed), because they are
global to the deployment, not per-queue.

| Key                          | Type   | Role                                                              |
| ---------------------------- | ------ | ---------------------------------------------------------------- |
| `<name>:queue`               | list   | FIFO of job ids. `LPUSH` head, `BRPOP` tail.                     |
| `<name>:queued`              | set    | Dedupe guard — an id is queued only once.                       |
| `<name>:processing`          | set    | Ids actively processing (observability).                        |
| `<name>:dead`                | set    | Dead-letter ids (terminal / attempts exhausted).                |
| `<name>:global_lock`         | string | Legacy single execution lock (`SET NX EX`, token).              |
| `lock:local`                 | string | The single local-CPU slot (cap 1).                              |
| `lock:auto_poll`             | string | A generic named lock; only one auto-poll tick runs at a time.   |
| `semaphore:cloud`            | zset   | Cloud slot tokens scored by expiry; counting semaphore.         |

These come straight from `RedisTranscriptionQueue.__init__`:

```python
self._list_key       = f"{queue_name}:queue"
self._set_key        = f"{queue_name}:queued"
self._processing_key = f"{queue_name}:processing"
self._dead_key       = f"{queue_name}:dead"
self._lock_key       = f"{queue_name}:global_lock"
self._local_lock_key = "lock:local"
self._cloud_sem_key  = "semaphore:cloud"
```

`lock:auto_poll` is not a fixed attribute — it is the **name** the auto-poll
thread passes to the generic `acquire_named_lock(name, ttl)` / `release_named_lock`.

## FIFO and dedupe (unchanged)

FIFO is `LPUSH` on enqueue (head) and `BRPOP` on dequeue (tail): the oldest id
is always served next. The dedupe `<name>:queued` set gates `enqueue` — the list
is pushed only when `SADD` reports a new member. See
[Redis Queue and Lock](09-redis-queue.md) for `enqueue` / `dequeue` /
`ensure_queued` / `requeue` in full.

## Token-safe locks

Every lock (global, named, and the local provider slot) follows the same pattern:
**acquire with a unique token, release only if you still own it.** Acquire is
`SET NX EX` with a fresh `uuid4().hex`; the first caller wins and receives the
token, everyone else gets `None`.

```python
def acquire_named_lock(self, name: str, ttl_seconds: int) -> str | None:
    token = uuid.uuid4().hex
    acquired = self._r.set(name, token, nx=True, ex=ttl_seconds)
    return token if acquired else None
```

Release is an **atomic compare-and-del** via a registered Lua script, so a worker
never frees a lock it has lost (e.g. the TTL expired and another worker grabbed
the key in the meantime):

```lua
-- RELEASE_IF_OWNER  (KEYS[1]=lock key, ARGV[1]=token)
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
```

`_release_token_lock` wraps that script and never raises — a transient Redis
error during release cannot kill the worker loop. The `EX` TTL is the safety net:
if a holder crashes, Redis expires the key so the deployment is never wedged.

## The cloud semaphore (atomic ZSET)

Cloud transcriptions run with **configurable concurrency** (default 5), unlike the
single local CPU slot. The semaphore is a `zset` (`semaphore:cloud`) whose members
are slot tokens, **scored by their expiry time**. Acquire is one EVAL so concurrent
workers can never exceed the cap:

```lua
-- ACQUIRE_CLOUD_SLOT  (KEYS[1]=semaphore zset; ARGV=[now, expiry, capacity, token])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])      -- reclaim expired slots
if tonumber(redis.call('ZCARD', KEYS[1])) < tonumber(ARGV[3]) then
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])               -- take a slot
  return ARGV[4]
end
return false
```

Three steps, atomically: drop slots whose score (expiry) is already past, check
`ZCARD < cap`, and only then `ZADD` the new token scored at `now + ttl`. There is
no race where five workers see four used slots and all grab the sixth. Release is
a plain `ZREM token` (also written to never crash the loop).

`acquire_provider_slot(kind, ttl)` dispatches on `kind`:

- `kind="local"` → `SET lock:local <token> NX EX <ttl>`, cap **1** (one CPU job).
- `kind="cloud"` → the Lua semaphore above, cap `CLOUD_TRANSCRIPTION_CONCURRENCY`.

It returns a token, or `None` when no slot is free. The queue loop must then
**requeue with a short delay — never fail the job** for lack of a slot.

A crashed worker's cloud slot is reclaimed only when its score (`now + ttl`)
passes, where `ttl` is `PROVIDER_LOCK_TTL_SECONDS` (default 14400 = 4 h) — safe
and conservative.

## Observability sets and stats

`mark_processing` / `clear_processing` move ids in and out of `<name>:processing`;
`mark_dead` / `remove_dead` / `dead_job_ids` manage `<name>:dead`. These are
**observability only** — Postgres `status='failed'` is the real dead-letter
record. `queue_stats()` powers the admin panel:

```python
def queue_stats(self) -> dict[str, int]:
    return {
        "queued":     int(self._r.llen(self._list_key)),
        "processing": int(self._r.scard(self._processing_key)),
        "dead":       int(self._r.scard(self._dead_key)),
    }
```

## `requeue_pending_jobs(now)` — backoff-aware reconciliation

`requeue_pending_jobs(repositories, queue, now=None)` rebuilds the queue from
Postgres. It iterates `pending` jobs and calls **`ensure_queued`** (not `enqueue`)
on each, so it also rescues ids orphaned in the dedupe set by a mid-dequeue crash.
When `now` is passed, jobs still in **retry backoff** are skipped:

```python
jobs = (
    repositories.jobs.list_pending_jobs(now)   # WHERE next_retry_at IS NULL OR <= now
    if now is not None
    else repositories.jobs.list_pending_jobs()
)
for job in jobs:
    if queue.ensure_queued(job.id):
        count += 1
```

So a retry that has been scheduled for later (`next_retry_at` in the future) is
never woken before its time. It is idempotent and runs at worker startup and on
every idle tick and auto-poll tick. See [Retries and Dead-letter](31-retries-dead-letter.md).

## In-memory parity

`InMemoryTranscriptionQueue` (`QUEUE_BACKEND=memory`, and the test fake) mirrors
every method in-process: a `deque` + dedupe `set`, a `threading.Lock` for the
locks, a `set` of cloud slot tokens capped at `cloud_concurrency`, a single
`_local_token`, and `_processing` / `_dead` sets. TTL is **not** enforced — there
is no cross-process expiry in one process — but the cap and token semantics match,
so semaphore behavior (5 acquire, 6th waits, release frees) is unit-tested without
a live Redis.

## Configuration

`QueueSettings.from_env` (`app/queue/config.py`):

| Variable                          | Default | Meaning                                          |
| --------------------------------- | ------- | ------------------------------------------------ |
| `CLOUD_TRANSCRIPTION_CONCURRENCY` | `5`     | Cloud semaphore capacity (`semaphore:cloud`).    |
| `LOCAL_TRANSCRIPTION_CONCURRENCY` | `1`     | Local CPU slot cap (`lock:local`).               |
| `PROVIDER_LOCK_TTL_SECONDS`       | `14400` | TTL/score of provider slots (slot reclaim).      |

All are validated as positive integers. See
[Provider Concurrency](30-provider-concurrency.md) for how the queue loop chooses
`kind` and acquires the matching slot, and
[Environment Variables](03-environment-variables.md) for the full list.

## Losing Redis is recoverable

Because Postgres is authoritative, a Redis flush or restart loses nothing
durable. The queue list, the dedupe/processing/dead sets, the locks, and the
semaphore are all rebuilt or simply re-acquired on demand: pending work is
re-enqueued from Postgres by `requeue_pending_jobs`, dead-letter state lives in
`status='failed'`, and locks/slots are taken fresh by the next worker tick. A
lost queue can only delay a job — never lose it.
