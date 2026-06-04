"""Redis-backed transcription queue + global execution lock.

Postgres remains the single source of truth. This package is only the wake-up
signal (which job to look at next) and the cross-process concurrency control
(process one transcription at a time on a CPU-bound VPS). If Redis ever loses the
queue, :func:`requeue_pending_jobs` rebuilds it from the pending jobs in Postgres.
"""

from __future__ import annotations

import logging

from app.queue.config import QueueSettings
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.queue.ports import TranscriptionQueue

LOGGER = logging.getLogger(__name__)

__all__ = [
    "QueueSettings",
    "TranscriptionQueue",
    "InMemoryTranscriptionQueue",
    "build_queue",
    "requeue_pending_jobs",
]


def build_queue(settings: QueueSettings) -> TranscriptionQueue | None:
    """Return the queue for the configured backend, or None for ``none`` (poll mode)."""
    if settings.backend == "redis":
        from app.queue.redis_queue import RedisTranscriptionQueue

        return RedisTranscriptionQueue.from_url(
            settings.redis_url, queue_name=settings.queue_name
        )
    if settings.backend == "memory":
        return InMemoryTranscriptionQueue()
    return None


def requeue_pending_jobs(repositories, queue: TranscriptionQueue) -> int:
    """Re-enqueue every Postgres ``pending`` job that is not already queued.

    Safe to call repeatedly (enqueue is deduped). Returns how many ids were newly
    enqueued. This is the self-heal path for a lost/empty Redis queue and the
    worker's startup reconciliation.
    """
    count = 0
    for job in repositories.jobs.list_pending_jobs():
        # ensure_queued (not enqueue) so a job orphaned in the dedupe set by a
        # mid-dequeue Redis failure is still re-pushed onto the list.
        if queue.ensure_queued(job.id):
            count += 1
    if count:
        LOGGER.info("Re-enqueued %s pending job(s) into the transcription queue", count)
    return count
