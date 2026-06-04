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

    def __init__(self) -> None:
        self._items: deque[int] = deque()
        self._queued: set[int] = set()
        self._cond = threading.Condition()
        self._lock_state = threading.Lock()
        self._lock_token: str | None = None
        self._token_seq = 0

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

    def queued_job_ids(self) -> set[int]:
        with self._cond:
            return set(self._queued)
