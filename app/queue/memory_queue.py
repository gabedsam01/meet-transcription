from __future__ import annotations

import threading
from collections import deque


class InMemoryTranscriptionQueue:
    """In-process queue + lock implementing :class:`TranscriptionQueue`.

    Used for single-process dev runs (``QUEUE_BACKEND=memory``) and as the faithful
    fake in tests. The "global lock" is a simple in-process mutex with an opaque
    token; ``ttl_seconds`` is accepted for API parity but not enforced (there is no
    cross-process expiry to worry about in one process).
    """

    def __init__(self, *, cloud_concurrency: int = 5) -> None:
        self._items: deque[int] = deque()
        self._queued: set[int] = set()
        self._cond = threading.Condition()
        self._lock_state = threading.Lock()
        self._lock_token: str | None = None
        self._token_seq = 0
        # Provider concurrency + observability state (TTL is irrelevant in-process).
        self._cloud_capacity = max(1, cloud_concurrency)
        self._cloud_slots: set[str] = set()
        self._local_token: str | None = None
        self._slot_seq = 0
        self._processing: set[int] = set()
        self._dead: set[int] = set()

    def enqueue(self, job_id: int) -> bool:
        with self._cond:
            if job_id in self._queued:
                return False
            self._queued.add(job_id)
            self._items.appendleft(job_id)  # head; dequeue pops the tail -> FIFO
            self._cond.notify()
            return True

    def dequeue(self, timeout: float = 0) -> int | None:
        with self._cond:
            if not self._items and timeout:
                self._cond.wait(timeout)
            if not self._items:
                return None
            job_id = self._items.pop()
            self._queued.discard(job_id)
            return job_id

    def ensure_queued(self, job_id: int) -> bool:
        with self._cond:
            # In-process the set and list never diverge, but check the list itself
            # for parity with the Redis adapter's self-healing reconcile.
            if job_id in self._items:
                self._queued.add(job_id)
                return False
            self._queued.add(job_id)
            self._items.appendleft(job_id)
            self._cond.notify()
            return True

    def requeue(self, job_id: int) -> None:
        with self._cond:
            if job_id in self._queued:
                return
            self._queued.add(job_id)
            self._items.append(job_id)  # tail: retried after the current head items
            self._cond.notify()

    def acquire_global_lock(self, ttl_seconds: int) -> str | None:
        with self._lock_state:
            if self._lock_token is not None:
                return None
            self._token_seq += 1
            self._lock_token = f"memlock-{self._token_seq}"
            return self._lock_token

    def release_global_lock(self, token: str) -> None:
        with self._lock_state:
            if self._lock_token == token:
                self._lock_token = None

    def acquire_provider_slot(self, kind: str, ttl_seconds: int) -> str | None:
        with self._lock_state:
            self._slot_seq += 1
            token = f"slot-{self._slot_seq}"
            if kind == "local":
                if self._local_token is not None:
                    return None
                self._local_token = token
                return token
            if len(self._cloud_slots) >= self._cloud_capacity:
                return None
            self._cloud_slots.add(token)
            return token

    def release_provider_slot(self, kind: str, token: str) -> None:
        with self._lock_state:
            if kind == "local":
                if self._local_token == token:
                    self._local_token = None
                return
            self._cloud_slots.discard(token)

    def mark_processing(self, job_id: int) -> None:
        with self._lock_state:
            self._processing.add(job_id)

    def clear_processing(self, job_id: int) -> None:
        with self._lock_state:
            self._processing.discard(job_id)

    def mark_dead(self, job_id: int) -> None:
        with self._lock_state:
            self._dead.add(job_id)

    def remove_dead(self, job_id: int) -> None:
        with self._lock_state:
            self._dead.discard(job_id)

    def dead_job_ids(self) -> set[int]:
        with self._lock_state:
            return set(self._dead)

    def queue_stats(self) -> dict[str, int]:
        with self._cond:
            queued = len(self._items)
        with self._lock_state:
            return {
                "queued": queued,
                "processing": len(self._processing),
                "dead": len(self._dead),
            }

    def queued_job_ids(self) -> set[int]:
        with self._cond:
            return set(self._queued)

    def health(self) -> bool:
        return True  # in-process: always reachable
