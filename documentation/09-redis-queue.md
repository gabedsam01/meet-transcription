# Redis Queue and Lock

> **Advanced queue, locks and semaphore.** This page describes the original
> single-global-lock model. In redis-queue mode that lock is superseded by
> **provider-aware concurrency** (cloud runs several in parallel, local CPU runs
> one) plus extra keys (`:processing`, `:dead`, `lock:local`, `lock:auto_poll`,
> `semaphore:cloud`). See [Advanced Redis queue](29-redis-queue-advanced.md),
> [Provider concurrency](30-provider-concurrency.md), and
> [Retries & dead-letter](31-retries-dead-letter.md).

meet-transcription runs on a CPU-bound VPS where local CPU transcription must run
**one at a time** (cloud providers may run several in parallel). The queue layer
(`app/queue/`) provides two things and only two things:

1. a **wake-up signal** — which job the worker should look at next, FIFO; and
2. a **single global execution lock** — at most one transcription runs across
   all worker processes/threads.

PostgreSQL remains the **single source of truth**. The queue is never
authoritative: if Redis loses the list, the worker rebuilds it from the
`pending` jobs in Postgres. A lost queue can delay a job, never lose it.

This page documents the exact Redis key layout, the enqueue/dequeue/lock
semantics, the dedupe + single-execution guarantees, the startup/idle
reconciliation, the orphan self-heal, the `QUEUE_BACKEND` modes, and the
behavior when Redis is down. It is grounded in `app/queue/__init__.py`,
`app/queue/redis_queue.py`, `app/queue/memory_queue.py`,
`app/queue/config.py`, `app/queue/ports.py`, and `app/worker/queue_loop.py`.

For the bigger picture, see [Architecture](01-architecture.md). For the worker
itself, see [Architecture](01-architecture.md) and
[Environment Variables](03-environment-variables.md).

## Where the queue fits

```
web (/jobs/run-once)            worker (python -m app.worker.main)
  validate + create pending       recover stale jobs
  job in Postgres                  reconcile pending -> queue (startup)
        |                          loop:  dequeue -> global lock -> claim_job
        v                                   -> process -> release lock
  queue.enqueue(job_id)  -----> Redis  <----  queue.dequeue(timeout)
        ^                       transcription:queue (list, FIFO)
        |                       transcription:queued (set, dedupe)
  Postgres = source of truth   transcription:global_lock (string, one runner)
```

The web service **never transcribes in-request** — `/jobs/run-once` only
validates settings, creates a `pending` job in Postgres, and best-effort
enqueues the job id. The worker consumes the queue, takes the global lock,
atomically claims the job in Postgres, and processes it.

## Redis key layout

The `queue_name` prefix defaults to `transcription` (env `QUEUE_NAME`). With
the default name, the three keys are:

| Key                            | Type   | Role                                                            |
| ------------------------------ | ------ | -------------------------------------------------------------- |
| `transcription:queue`          | list   | The FIFO of job ids. `LPUSH` at the head, `BRPOP` at the tail. |
| `transcription:queued`         | set    | Dedupe guard — a job id is in the queue only once.             |
| `transcription:global_lock`    | string | The single execution lock; `SET NX EX` with an opaque token.   |

These names come directly from `RedisTranscriptionQueue.__init__`:

```python
self._list_key = f"{queue_name}:queue"
self._set_key  = f"{queue_name}:queued"
self._lock_key = f"{queue_name}:global_lock"
```

FIFO ordering is the combination of `LPUSH` (push to the head) on enqueue and
`BRPOP` (blocking pop from the tail) on dequeue: the oldest id is always the one
returned next.

Inspecting the live state from a shell inside the `redis` container:

```bash
# how many jobs are waiting
docker compose exec redis redis-cli LLEN transcription:queue

# the waiting job ids (head .. tail; tail is dequeued next)
docker compose exec redis redis-cli LRANGE transcription:queue 0 -1

# the dedupe set (ids considered "already queued")
docker compose exec redis redis-cli SMEMBERS transcription:queued

# is a transcription running right now? (token + remaining TTL in seconds)
docker compose exec redis redis-cli GET transcription:global_lock
docker compose exec redis redis-cli TTL transcription:global_lock
```

## Queue operations (the `TranscriptionQueue` contract)

The contract is the `TranscriptionQueue` Protocol in `app/queue/ports.py`. Both
backends (`RedisTranscriptionQueue`, `InMemoryTranscriptionQueue`) implement it
identically in behavior.

### `enqueue(job_id) -> bool`

Add a job id to the queue, deduped. The **set is the gate**: `SADD` runs first,
and the list `LPUSH` happens only when the id was newly added.

```python
def enqueue(self, job_id: int) -> bool:
    if self._r.sadd(self._set_key, job_id) == 1:   # newly added?
        self._r.lpush(self._list_key, job_id)      # only then push to head
        return True
    return False                                    # already queued -> no-op
```

Returns `True` if it was newly queued, `False` if the id was already present.
Called by the web on `/jobs/run-once` for the freshly created job id.

### `dequeue(timeout) -> int | None`

Block on `BRPOP` for up to `timeout` seconds (0 = no wait), pop the oldest id
from the tail, and remove it from the dedupe set.

```python
def dequeue(self, timeout: float = 0) -> int | None:
    result = self._r.brpop(self._list_key, timeout=int(timeout or 0))
    if result is None:
        return None
    _key, raw = result
    job_id = int(raw)
    self._r.srem(self._set_key, job_id)   # clear the dedupe gate
    return job_id
```

Returns `None` when nothing arrives within `timeout` — the worker treats that
as an idle tick. In the worker, `timeout` defaults to
`WORKER_POLL_INTERVAL_SECONDS` (`container.settings.poll_interval_seconds`).

### `ensure_queued(job_id) -> bool` — the reconcile/self-heal path

Unlike `enqueue` (which trusts the dedupe set), `ensure_queued` trusts the
**list**. It uses `LPOS` to check whether the id is genuinely present in
`transcription:queue`; if it is not, it re-pushes it (and re-asserts the set).

```python
def ensure_queued(self, job_id: int) -> bool:
    if self._r.lpos(self._list_key, job_id) is None:   # not actually in the list
        self._r.sadd(self._set_key, job_id)
        self._r.lpush(self._list_key, job_id)
        return True
    self._r.sadd(self._set_key, job_id)   # in the list; just keep the set consistent
    return False
```

This is what makes the dedupe set safe. If a previous `dequeue` crashed
**between `BRPOP` and `SREM`**, the id is stranded in `transcription:queued`
but absent from `transcription:queue`. A plain `enqueue` would see it in the set
and refuse to re-push it (`SADD` returns 0), leaving the job invisible to the
queue. `ensure_queued` looks past the set, sees the id is not in the list via
`LPOS`, and re-pushes it. Postgres is the source of truth, so a `pending` job
can never be permanently stuck unqueued.

### `requeue(job_id) -> None`

Put a job back when it could not run right now — the global lock was held by
another worker. It is pushed to the **tail** (`RPUSH`) so it is retried after
the items currently ahead of it, not jumped to the front.

```python
def requeue(self, job_id: int) -> None:
    if self._r.sadd(self._set_key, job_id) == 1:
        self._r.rpush(self._list_key, job_id)   # tail: retried after current items
```

### `queued_job_ids() -> set[int]` and `health() -> bool`

`queued_job_ids()` returns a snapshot of `SMEMBERS transcription:queued` (used
for introspection and tests). `health()` is a `PING` that **never raises** — it
returns `False` on any error, which the web uses to render queue status.

## The global execution lock

Only one transcription runs at a time across the whole deployment. That is
enforced by `transcription:global_lock`, a Redis string set with `NX` (only if
absent) and `EX` (TTL):

```python
def acquire_global_lock(self, ttl_seconds: int) -> str | None:
    token = uuid.uuid4().hex
    acquired = self._r.set(self._lock_key, token, nx=True, ex=ttl_seconds)
    return token if acquired else None
```

- The first caller to `SET NX` wins and gets an **opaque token** (a UUID hex).
- Every other caller gets `None` (the key already exists) and must back off.
- The `EX` TTL is `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS` (default **14400** =
  4 hours). If a worker dies mid-job and never releases the lock, the TTL lets
  Redis auto-expire it so the system is never wedged forever.

Release is a **compare-and-delete** so a worker never frees a lock it has lost
(for example after the TTL expired and another worker grabbed the key):

```python
def release_global_lock(self, token: str) -> None:
    try:
        if self._r.get(self._lock_key) == token:
            self._r.delete(self._lock_key)
    except Exception:   # releasing must never crash the worker loop
        LOGGER.warning("Could not release the global transcription lock")
```

Release is best-effort and never raises — a transient Redis error during
release cannot kill the worker loop.

## The worker queue loop

`run_queue_loop` in `app/worker/queue_loop.py` is the consumer. One iteration:

1. `job_id = queue.dequeue(timeout)`. If `None` (idle), run the idle callback
   (reconcile, see below) and continue.
2. `token = queue.acquire_global_lock(container.queue_lock_ttl)`.
   - If `None`, another worker holds the lock: `queue.requeue(job_id)` (back to
     the tail), wait briefly (`on_contention`, default `stop_event.wait(1)`),
     and continue.
3. With the lock held, `job = repositories.jobs.claim_job(job_id, worker_id, now())`.
   - `claim_job` is the **atomic `pending -> processing`** transition in
     Postgres — the **final dedupe defense**. If it returns `None`, the job is
     no longer `pending` (already claimed/completed/failed); skip it.
4. `processor.process(job)` runs the actual download/transcribe/upload/save.
   A failure here is logged but **never** kills the loop.
5. `finally: queue.release_global_lock(token)` — the lock is always released.

```python
while not stop_event.is_set():
    try:
        job_id = queue.dequeue(timeout)
        if job_id is None:
            idle()                      # reconcile pending jobs from Postgres
            continue
        token = queue.acquire_global_lock(container.queue_lock_ttl)
        if token is None:
            queue.requeue(job_id)       # someone else is transcribing; retry later
            contention()
            continue
        try:
            job = container.repositories.jobs.claim_job(job_id, worker_id, now())
            if job is None:
                continue                # no longer pending; drop it
            try:
                proc.process(job)
            except Exception:
                LOGGER.exception("Unhandled error processing job_id=%s", job_id)
        finally:
            queue.release_global_lock(token)
    except Exception:                   # survive transient queue/database errors
        LOGGER.exception("Queue worker iteration failed worker=%s", worker_id)
        error_backoff()                 # default: stop_event.wait(min(timeout or 1, 5))
```

`WORKER_CONCURRENCY` threads can run this loop, but the **global lock keeps real
transcription serialized** — extra threads dequeue and contend, they do not run
two transcriptions at once.

## The three guarantees

| Guarantee                | How it is enforced                                                                                                                                                  |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **FIFO ordering**        | `enqueue` does `LPUSH` (head), `dequeue` does `BRPOP` (tail). The oldest id is always served next. `requeue` uses `RPUSH` (tail) so a deferred job keeps its place. |
| **Dedupe (queued once)** | `enqueue` adds to the list only when `SADD` reports a new member (`== 1`). The same `job_id` is never on the list twice while waiting.                              |
| **Single execution**     | `acquire_global_lock` is `SET NX EX`: exactly one holder, with a TTL safety net. `release_global_lock` is compare-and-delete. The worker holds it around every job. |

The dedupe set is an *optimization* for the wake-up signal; the **definitive**
guard against double-processing is `JobRepository.claim_job` (atomic
`pending -> processing` in Postgres). Even if two workers somehow dequeued the
same id, only one `claim_job` succeeds; the other gets `None` and skips.

## Reconciliation: `requeue_pending_jobs`

`requeue_pending_jobs(repositories, queue)` in `app/queue/__init__.py` rebuilds
the queue from Postgres. It iterates every `pending` job
(`repositories.jobs.list_pending_jobs()`) and calls **`ensure_queued`** (not
`enqueue`) on each, so it also rescues ids orphaned in the dedupe set:

```python
def requeue_pending_jobs(repositories, queue) -> int:
    count = 0
    for job in repositories.jobs.list_pending_jobs():
        if queue.ensure_queued(job.id):
            count += 1
    return count
```

It is **idempotent** — calling it repeatedly is safe because `ensure_queued`
re-adds only ids that are genuinely missing from the list. It runs at two
moments:

- **At worker startup** — after recovering stale `processing` jobs,
  `app.worker.main.run` calls it once to repopulate a queue that may have been
  lost while the worker was down:

  ```python
  if container.queue is not None:
      enqueued = requeue_pending_jobs(container.repositories, container.queue)
      LOGGER.info("Queue mode: reconciled %s pending job(s) at startup", enqueued)
      loop = run_queue_loop
  else:
      loop = run_worker_loop
  ```

- **While idle** — every time `dequeue` returns `None`, the queue loop runs its
  idle callback, which defaults to `requeue_pending_jobs`. So a job that
  Postgres has as `pending` but Redis never received (e.g. enqueued while Redis
  was down) is picked up on the next idle tick, no restart required.

## Orphan self-heal (BRPOP/SREM crash window)

The only window where the list and the dedupe set can diverge is a crash
**between `BRPOP` (dequeue popped the id) and `SREM` (clearing the set)**. After
such a crash:

- `transcription:queue` (list): **does not** contain the id (it was popped).
- `transcription:queued` (set): **still** contains the id (`SREM` never ran).

A plain `enqueue` would see the id in the set and refuse to push it. The
self-heal is `ensure_queued`'s use of **`LPOS`** to check the list directly:

```python
if self._r.lpos(self._list_key, job_id) is None:   # truly absent from the list
    self._r.sadd(self._set_key, job_id)
    self._r.lpush(self._list_key, job_id)           # re-push it
    return True
```

Because reconciliation goes through `ensure_queued`, every such orphan is
re-pushed on the next startup or idle tick. Combined with Postgres being the
source of truth, no `pending` job can be lost to a mid-dequeue crash.

## `QUEUE_BACKEND` modes

`QueueSettings.from_env` (`app/queue/config.py`) reads `QUEUE_BACKEND` and
validates it against `("none", "memory", "redis")`. `build_queue` then returns
the matching implementation:

| `QUEUE_BACKEND` | Implementation                              | Behavior                                                                                                                                                  |
| --------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `redis`         | `RedisTranscriptionQueue` (via `from_url`)  | Production. Real Redis list/set/lock; cross-process FIFO + global lock. **`docker-compose.yml` sets this.**                                                |
| `memory`        | `InMemoryTranscriptionQueue`                | Single-process dev runs and the faithful test fake. In-process `deque` + `set` + a `threading.Lock`. `ttl_seconds` is accepted but **not** enforced.       |
| `none`          | `None` (no queue object)                    | **Default in code.** The worker keeps the **legacy poll loop** (`run_worker_loop`), claiming the next pending job directly via `claim_next_pending_job`. |

Defaults to know:

- **Code default is `none`** — an unconfigured worker keeps the legacy poll
  behavior unchanged.
- **`docker-compose.yml` default is `redis`** — the composed stack runs the
  Redis-backed queue and lock.

```python
def build_queue(settings: QueueSettings) -> TranscriptionQueue | None:
    if settings.backend == "redis":
        from app.queue.redis_queue import RedisTranscriptionQueue
        return RedisTranscriptionQueue.from_url(settings.redis_url, queue_name=settings.queue_name)
    if settings.backend == "memory":
        return InMemoryTranscriptionQueue()
    return None
```

Related env vars (see [Environment Variables](03-environment-variables.md)):

| Variable                                  | Default                  | Meaning                                              |
| ----------------------------------------- | ------------------------ | ---------------------------------------------------- |
| `QUEUE_BACKEND`                           | `none` (code)            | `redis` \| `memory` \| `none`.                       |
| `REDIS_URL`                               | `redis://redis:6379/0`   | Redis connection URL for the `redis` backend.        |
| `QUEUE_NAME`                              | `transcription`          | Key prefix for the list/set/lock keys.               |
| `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS`   | `14400` (4 h)            | TTL of the global execution lock; must be > 0.       |

### The in-memory backend

`InMemoryTranscriptionQueue` mirrors the Redis semantics in one process: a
`deque` (head = `appendleft`, tail = `pop` -> FIFO), a `set` for dedupe, a
`threading.Condition` so `dequeue(timeout)` can block, and a `threading.Lock`
for the global lock (an in-process mutex with an opaque `memlock-N` token). Its
`ensure_queued` checks the list itself for parity with the Redis self-heal, and
`health()` always returns `True` (in-process is always reachable). It enforces
**no** TTL — there is no cross-process expiry to worry about in a single
process.

## Failure behavior: Redis down

The whole design assumes Redis can vanish without losing work, because Postgres
is authoritative.

**Bounded timeouts so a down Redis fails fast.** The Redis client is built with
short socket timeouts so neither a web request nor the worker loop hangs:

```python
client = Redis.from_url(
    redis_url, decode_responses=True,
    socket_connect_timeout=3, socket_timeout=3,
)
```

**Enqueue failure (web).** `/jobs/run-once` enqueues best-effort. If Redis is
down, the exception is caught, logged, and the job **stays `pending` in
Postgres** — it is never lost, and the route never returns a 500:

```python
if result.status == "created" and result.job is not None and app.state.queue is not None:
    try:
        app.state.queue.enqueue(result.job.id)
    except Exception:   # Postgres is the source of truth; never 500 here.
        logging.getLogger(__name__).exception(
            "Could not enqueue job_id=%s; it stays pending for the worker to reconcile",
            result.job.id,
        )
        flash_message = (
            "Fila indisponível no momento: a transcrição foi registrada e será "
            "processada assim que a fila voltar."
        )
```

The pending job is later picked up by the worker's startup or idle
reconciliation as soon as Redis returns.

**Worker-side failures.** Any transient Redis/Postgres error in a loop iteration
(`dequeue`, reconcile, lock, claim) is caught; the worker logs, backs off
(`error_backoff`, default `stop_event.wait(min(timeout or 1, 5))`), and recovers
on the next iteration. A daemon worker thread is never killed by a transient
failure. `release_global_lock` and `health()` are written to never raise.

**Lock TTL.** If a worker crashes while holding `transcription:global_lock`, the
`EX` TTL (`TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS`, default 4 h) lets Redis expire
the key so the deployment is never permanently blocked.

**UI status.** The dashboard and jobs pages reflect queue state via
`_queue_status()` in `app/web/main.py`:

| State                         | `_queue_status()` result               | UI                                                            |
| ----------------------------- | --------------------------------------- | ------------------------------------------------------------ |
| `QUEUE_BACKEND=none`          | `{"mode": "poll", "available": None}`   | Poll mode (no Redis).                                         |
| Redis backend, `health()` OK  | `{"mode": "queue", "available": True}`  | Queue online.                                                 |
| Redis backend, `health()` bad | `{"mode": "queue", "available": False}` | Queue unavailable warning (the probe never 500s the page).   |

## Operational checklist

```bash
# Is the queue backend wired in the running services?
docker compose exec worker printenv QUEUE_BACKEND REDIS_URL QUEUE_NAME
docker compose exec web    printenv QUEUE_BACKEND REDIS_URL QUEUE_NAME

# Is Redis healthy?
docker compose exec redis redis-cli PING            # -> PONG

# Backlog depth and the next id to run (tail of the list)
docker compose exec redis redis-cli LLEN  transcription:queue
docker compose exec redis redis-cli LRANGE transcription:queue 0 -1

# Is a transcription currently running, and for how much longer?
docker compose exec redis redis-cli GET transcription:global_lock
docker compose exec redis redis-cli TTL transcription:global_lock

# Force a rebuild from Postgres: restart the worker (it reconciles on startup),
# or just wait — it reconciles on every idle dequeue tick.
docker compose restart worker
```

If the queue and Postgres ever disagree, **trust Postgres**: any `pending` job
will be re-enqueued automatically by `requeue_pending_jobs` at worker startup
and on the next idle tick.
