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

    def acquire_provider_slot(self, kind: str, ttl_seconds: int) -> str | None:
        """Acquire one concurrency slot for ``kind`` ('cloud' or 'local').

        'local' is a single token lock (one CPU transcription at a time). 'cloud'
        is a counting semaphore capped at the configured cloud concurrency. Returns
        an opaque token, or None when no slot is free (the caller requeues with a
        short delay — it must never fail the job for lack of a slot)."""

    def release_provider_slot(self, kind: str, token: str) -> None:
        """Release a slot previously acquired for ``kind`` (token-checked)."""

    def mark_processing(self, job_id: int) -> None:
        """Record that ``job_id`` is actively processing (observability)."""

    def clear_processing(self, job_id: int) -> None:
        """Drop ``job_id`` from the processing set when it reaches a terminal state."""

    def mark_dead(self, job_id: int) -> None:
        """Add ``job_id`` to the dead-letter set (terminal/exhausted). Observability;
        Postgres status='failed' stays the source of truth."""

    def remove_dead(self, job_id: int) -> None:
        """Remove ``job_id`` from the dead-letter set (manual retry)."""

    def dead_job_ids(self) -> set[int]:
        """Snapshot of dead-letter ids."""

    def queue_stats(self) -> dict[str, int]:
        """``{'queued': n, 'processing': n, 'dead': n}`` for the observability panel."""

    def queued_job_ids(self) -> set[int]:
        """Snapshot of currently-queued ids (introspection / tests)."""

    def health(self) -> bool:
        """True if the backing store is reachable (Redis PING). Never raises."""
