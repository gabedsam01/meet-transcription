from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TranscriptionQueue(Protocol):
    """A FIFO job queue with a single global execution lock.

    Postgres stays the source of truth; this queue is only the wake-up signal and
    the cross-process concurrency control. ``enqueue`` is deduped so the same
    ``job_id`` is never queued twice; the final defense against double-processing
    is ``JobRepository.claim_job`` (pending -> processing is atomic in Postgres).
    """

    def enqueue(self, job_id: int) -> bool:
        """Add ``job_id`` to the tail. Return False if it was already queued."""

    def ensure_queued(self, job_id: int) -> bool:
        """Re-queue ``job_id`` unless it is genuinely present in the list.

        Used by reconciliation. Unlike ``enqueue`` (which trusts the dedupe set),
        this checks the list itself, so it self-heals an id orphaned in the dedupe
        set by a mid-dequeue failure. Returns True if it was (re-)added."""

    def dequeue(self, timeout: float = 0) -> int | None:
        """Pop the oldest job id, blocking up to ``timeout`` seconds (0 = no wait).

        Return None when nothing is available within ``timeout``.
        """

    def requeue(self, job_id: int) -> None:
        """Put a job back (e.g. the global lock was held by another worker)."""

    def acquire_global_lock(self, ttl_seconds: int) -> str | None:
        """Acquire the single execution lock. Return an opaque token, or None when
        another holder already owns it. The token must be passed to release."""

    def release_global_lock(self, token: str) -> None:
        """Release the lock only if ``token`` still owns it (no foreign release)."""

    def queued_job_ids(self) -> set[int]:
        """Snapshot of currently-queued ids (introspection / tests)."""

    def health(self) -> bool:
        """True if the backing store is reachable (Redis PING). Never raises."""
