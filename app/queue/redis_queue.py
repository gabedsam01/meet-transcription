from __future__ import annotations

import logging
import time
import uuid

LOGGER = logging.getLogger(__name__)

# Atomic cloud-semaphore acquire: reclaim expired slots, then take one if the
# count is under capacity. A single EVAL so concurrent workers can never exceed
# the cap. KEYS[1]=semaphore zset; ARGV=[now, expiry, capacity, token].
_ACQUIRE_CLOUD_SLOT = """
-- ACQUIRE_CLOUD_SLOT
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if tonumber(redis.call('ZCARD', KEYS[1])) < tonumber(ARGV[3]) then
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
  return ARGV[4]
end
return false
"""

# Atomic token-checked release for a string lock: delete only if we still own it.
# KEYS[1]=lock key; ARGV[1]=token.
_RELEASE_IF_OWNER = """
-- RELEASE_IF_OWNER
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class RedisTranscriptionQueue:
    """Redis-backed :class:`TranscriptionQueue`.

    Key layout (``queue_name`` defaults to ``transcription``):

    - ``<name>:queue``        a list; LPUSH at the head, BRPOP from the tail (FIFO).
    - ``<name>:queued``       a set; dedupe guard so a job id is queued only once.
    - ``<name>:processing``   a set; ids actively processing (observability).
    - ``<name>:dead``         a set; dead-letter ids (terminal/exhausted).
    - ``<name>:global_lock``  a string with TTL; the legacy single execution lock.
    - ``lock:local``          a string with TTL; the single local-CPU slot.
    - ``semaphore:cloud``     a zset of slot tokens scored by expiry; cloud slots.

    ``redis`` is imported lazily so importing this module (and running its tests
    against a fake client) never requires redis-py or a live server.
    """

    def __init__(
        self,
        client,
        *,
        queue_name: str = "transcription",
        cloud_concurrency: int = 5,
        time_fn=time.time,
    ) -> None:
        self._r = client
        self._list_key = f"{queue_name}:queue"
        self._set_key = f"{queue_name}:queued"
        self._processing_key = f"{queue_name}:processing"
        self._dead_key = f"{queue_name}:dead"
        self._lock_key = f"{queue_name}:global_lock"
        self._local_lock_key = "lock:local"
        self._cloud_sem_key = "semaphore:cloud"
        self._cloud_capacity = max(1, cloud_concurrency)
        self._time_fn = time_fn
        self._acquire_cloud = None  # lazily registered Lua scripts
        self._release_if_owner = None

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        queue_name: str = "transcription",
        cloud_concurrency: int = 5,
    ) -> "RedisTranscriptionQueue":
        from redis import Redis  # lazy: only production / redis-backed runs need it

        # Bounded timeouts so a down Redis fails fast (UI health probe, enqueue)
        # instead of hanging a request or the worker loop.
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        return cls(client, queue_name=queue_name, cloud_concurrency=cloud_concurrency)

    # --- queue ---------------------------------------------------------------

    def enqueue(self, job_id: int) -> bool:
        # SADD first: it is the dedupe gate. Only push to the list if newly added.
        if self._r.sadd(self._set_key, job_id) == 1:
            self._r.lpush(self._list_key, job_id)
            return True
        return False

    def dequeue(self, timeout: float = 0) -> int | None:
        result = self._r.brpop(self._list_key, timeout=int(timeout or 0))
        if result is None:
            return None
        _key, raw = result
        job_id = int(raw)
        self._r.srem(self._set_key, job_id)
        return job_id

    def ensure_queued(self, job_id: int) -> bool:
        # Reconcile/self-heal path: trust the LIST, not the dedupe set. If a prior
        # dequeue crashed between BRPOP and SREM, the id is stranded in the set but
        # absent from the list; LPOS detects that and we re-push it. Postgres stays
        # the source of truth, so a pending job can never be stuck unqueued.
        if self._r.lpos(self._list_key, job_id) is None:
            self._r.sadd(self._set_key, job_id)
            self._r.lpush(self._list_key, job_id)
            return True
        self._r.sadd(self._set_key, job_id)  # keep the dedupe set consistent
        return False

    def requeue(self, job_id: int) -> None:
        if self._r.sadd(self._set_key, job_id) == 1:
            self._r.rpush(self._list_key, job_id)  # tail: retried after current items

    # --- global lock (legacy one-at-a-time) ----------------------------------

    def acquire_global_lock(self, ttl_seconds: int) -> str | None:
        token = uuid.uuid4().hex
        acquired = self._r.set(self._lock_key, token, nx=True, ex=ttl_seconds)
        return token if acquired else None

    def release_global_lock(self, token: str) -> None:
        self._release_token_lock(self._lock_key, token, "global transcription lock")

    # --- provider concurrency slots ------------------------------------------

    def acquire_provider_slot(self, kind: str, ttl_seconds: int) -> str | None:
        token = uuid.uuid4().hex
        if kind == "local":
            acquired = self._r.set(self._local_lock_key, token, nx=True, ex=ttl_seconds)
            return token if acquired else None
        # cloud: counting semaphore over a zset, acquired atomically via Lua.
        now = self._time_fn()
        result = self._acquire_cloud_script()(
            keys=[self._cloud_sem_key],
            args=[now, now + ttl_seconds, self._cloud_capacity, token],
        )
        return token if result else None

    def release_provider_slot(self, kind: str, token: str) -> None:
        if kind == "local":
            self._release_token_lock(self._local_lock_key, token, "local provider lock")
            return
        try:
            self._r.zrem(self._cloud_sem_key, token)
        except Exception:  # noqa: BLE001 - releasing must never crash the worker loop
            LOGGER.warning("Could not release a cloud semaphore slot")

    # --- observability sets --------------------------------------------------

    def mark_processing(self, job_id: int) -> None:
        self._r.sadd(self._processing_key, job_id)

    def clear_processing(self, job_id: int) -> None:
        self._r.srem(self._processing_key, job_id)

    def mark_dead(self, job_id: int) -> None:
        self._r.sadd(self._dead_key, job_id)

    def remove_dead(self, job_id: int) -> None:
        self._r.srem(self._dead_key, job_id)

    def dead_job_ids(self) -> set[int]:
        return {int(member) for member in self._r.smembers(self._dead_key)}

    def queue_stats(self) -> dict[str, int]:
        return {
            "queued": int(self._r.llen(self._list_key)),
            "processing": int(self._r.scard(self._processing_key)),
            "dead": int(self._r.scard(self._dead_key)),
        }

    def queued_job_ids(self) -> set[int]:
        return {int(member) for member in self._r.smembers(self._set_key)}

    def health(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:  # noqa: BLE001 - a health probe must never raise
            return False

    # --- internals -----------------------------------------------------------

    def _acquire_cloud_script(self):
        if self._acquire_cloud is None:
            self._acquire_cloud = self._r.register_script(_ACQUIRE_CLOUD_SLOT)
        return self._acquire_cloud

    def _release_token_lock(self, key: str, token: str, label: str) -> None:
        # Atomic compare-and-del so a worker never frees a lock it has lost (e.g.
        # after the TTL expired and another worker grabbed it).
        try:
            if self._release_if_owner is None:
                self._release_if_owner = self._r.register_script(_RELEASE_IF_OWNER)
            self._release_if_owner(keys=[key], args=[token])
        except Exception:  # noqa: BLE001 - releasing must never crash the worker loop
            LOGGER.warning("Could not release the %s", label)
