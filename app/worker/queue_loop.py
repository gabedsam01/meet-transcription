from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.queue import requeue_pending_jobs
from app.worker.container import WorkerContainer
from app.worker.processor import JobProcessor

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_queue_loop(
    container: WorkerContainer,
    stop_event: threading.Event,
    worker_id: str,
    processor: JobProcessor | None = None,
    now=_utc_now,
    dequeue_timeout: float | None = None,
    on_idle=None,
    on_contention=None,
) -> None:
    """Redis-queue worker loop: one transcription at a time, globally locked.

    Each iteration pops a job id, takes the single global execution lock, then
    atomically claims *that* job in Postgres (``claim_job``). The Postgres claim is
    the final dedupe defense; the lock serializes transcription across worker
    processes/threads on a CPU-bound VPS. When idle, pending Postgres jobs are
    re-enqueued (self-heal for a lost queue).
    """
    queue = container.queue
    proc = processor or JobProcessor(container)
    timeout = (
        dequeue_timeout
        if dequeue_timeout is not None
        else container.settings.poll_interval_seconds
    )
    idle = on_idle if on_idle is not None else (
        lambda: requeue_pending_jobs(container.repositories, queue)
    )
    contention = on_contention if on_contention is not None else (
        lambda: stop_event.wait(1)
    )

    while not stop_event.is_set():
        job_id = queue.dequeue(timeout)
        if job_id is None:
            idle()
            continue
        token = queue.acquire_global_lock(container.queue_lock_ttl)
        if token is None:
            # Another worker holds the single execution lock; put the job back.
            queue.requeue(job_id)
            contention()
            continue
        try:
            job = container.repositories.jobs.claim_job(job_id, worker_id, now())
            if job is None:
                LOGGER.info("Queued job_id=%s is no longer pending; skipping", job_id)
                continue
            LOGGER.info("Claimed job_id=%s worker=%s", job.id, worker_id)
            try:
                proc.process(job)
            except Exception:  # noqa: BLE001 - a single job must never kill the loop.
                LOGGER.exception("Unhandled error processing job_id=%s", job_id)
        finally:
            queue.release_global_lock(token)
