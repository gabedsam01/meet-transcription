from __future__ import annotations

import logging
import uuid

LOGGER = logging.getLogger(__name__)


class RedisTranscriptionQueue:
    """Redis-backed :class:`TranscriptionQueue`.

    Key layout (``queue_name`` defaults to ``transcription``):

    - ``<name>:queue``        a list; LPUSH at the head, BRPOP from the tail (FIFO).
    - ``<name>:queued``       a set; dedupe guard so a job id is queued only once.
    - ``<name>:global_lock``  a string with TTL; the single execution lock.

    ``redis`` is imported lazily so importing this module (and running its tests
    against a fake client) never requires redis-py or a live server.
    """

    def __init__(self, client, *, queue_name: str = "transcription") -> None:
        self._r = client
        self._list_key = f"{queue_name}:queue"
        self._set_key = f"{queue_name}:queued"
        self._lock_key = f"{queue_name}:global_lock"

    @classmethod
    def from_url(
        cls, redis_url: str, *, queue_name: str = "transcription"
    ) -> "RedisTranscriptionQueue":
        from redis import Redis  # lazy: only production / redis-backed runs need it

        client = Redis.from_url(redis_url, decode_responses=True)
        return cls(client, queue_name=queue_name)

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

    def acquire_global_lock(self, ttl_seconds: int) -> str | None:
        token = uuid.uuid4().hex
        acquired = self._r.set(self._lock_key, token, nx=True, ex=ttl_seconds)
        return token if acquired else None

    def release_global_lock(self, token: str) -> None:
        # Best-effort compare-and-delete so a worker never frees a lock it has lost
        # (e.g. after the TTL expired and another worker grabbed it).
        try:
            if self._r.get(self._lock_key) == token:
                self._r.delete(self._lock_key)
        except Exception:  # noqa: BLE001 - releasing must never crash the worker loop
            LOGGER.warning("Could not release the global transcription lock")

    def queued_job_ids(self) -> set[int]:
        return {int(member) for member in self._r.smembers(self._set_key)}
