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
    on_error=None,
) -> None:
    """Redis-queue worker loop with provider-aware concurrency.

    Each iteration pops a job id, resolves its provider to decide the concurrency
    *kind* (cloud or local), acquires the matching slot (cloud = a semaphore capped
    at ``CLOUD_TRANSCRIPTION_CONCURRENCY``; local = a single lock), then atomically
    claims *that* job in Postgres and processes it. When no slot is free the job is
    requeued with a short backoff — never failed. Multiple consumer threads run this
    loop; the slots (not a single global lock) bound parallelism per provider kind.
    """
    queue = container.queue
    proc = processor or JobProcessor(container)
    ttl = container.provider_lock_ttl
    timeout = (
        dequeue_timeout
        if dequeue_timeout is not None
        else container.settings.poll_interval_seconds
    )
    idle = on_idle if on_idle is not None else (
        lambda: requeue_pending_jobs(container.repositories, queue, now())
    )
    contention = on_contention if on_contention is not None else (
        lambda: stop_event.wait(1)
    )
    error_backoff = on_error if on_error is not None else (
        lambda: stop_event.wait(min(timeout or 1, 5))
    )

    while not stop_event.is_set():
        try:
            job_id = queue.dequeue(timeout)
            if job_id is None:
                idle()
                continue
            job = container.repositories.jobs.get_job(job_id)
            if job is None or job.status != "pending":
                # Stale/duplicate delivery: claim would no-op. Drop it; the
                # reconciler re-enqueues a genuinely-pending job when it is due.
                continue

            resolved = _safe_resolve(proc, job)
            if resolved is None:
                # Terminal provider/config error: own the job, then dead-letter it.
                claimed = container.repositories.jobs.claim_job(job_id, worker_id, now())
                if claimed is not None:
                    proc.process(claimed)  # re-resolves, fails, dead-letters (no retry)
                continue

            token = queue.acquire_provider_slot(resolved.kind, ttl)
            if token is None:
                # No slot for this provider kind right now — try later, never fail.
                queue.requeue(job_id)
                contention()
                continue
            try:
                claimed = container.repositories.jobs.claim_job(job_id, worker_id, now())
                if claimed is None:
                    LOGGER.info("Queued job_id=%s is no longer claimable; skipping", job_id)
                    continue
                LOGGER.info(
                    "Claimed job_id=%s worker=%s kind=%s", claimed.id, worker_id, resolved.kind
                )
                queue.mark_processing(claimed.id)
                try:
                    proc.process(claimed, resolved)
                except Exception:  # noqa: BLE001 - a single job must never kill the loop.
                    LOGGER.exception("Unhandled error processing job_id=%s", job_id)
                finally:
                    queue.clear_processing(claimed.id)
            finally:
                queue.release_provider_slot(resolved.kind, token)
        except Exception:  # noqa: BLE001 - survive transient queue/database errors.
            LOGGER.exception("Queue worker iteration failed worker=%s", worker_id)
            error_backoff()


def _safe_resolve(proc: JobProcessor, job):
    """Resolve the provider for slot selection; None on a terminal resolve error.

    A None result tells the loop to claim-and-dead-letter the job (process()
    re-resolves and routes the same terminal error through the failure handler)."""
    try:
        return proc.resolve(job)
    except Exception as exc:  # noqa: BLE001 - terminal; handled by claim + process().
        LOGGER.info("Provider resolution failed for job_id=%s: %s", job.id, exc)
        return None
