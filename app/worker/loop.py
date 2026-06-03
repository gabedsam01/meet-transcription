from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.worker.container import WorkerContainer
from app.worker.processor import JobProcessor

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_worker_loop(
    container: WorkerContainer,
    stop_event: threading.Event,
    worker_id: str,
    processor: JobProcessor | None = None,
    now=_utc_now,
    sleep=None,
) -> None:
    proc = processor or JobProcessor(container)
    # Default sleep returns as soon as stop is set, enabling fast shutdown.
    sleeper = sleep or stop_event.wait
    while not stop_event.is_set():
        job = container.repositories.jobs.claim_next_pending_job(worker_id, now())
        if job is None:
            sleeper(container.settings.poll_interval_seconds)
            continue
        LOGGER.info("Claimed job_id=%s worker=%s", job.id, worker_id)
        try:
            proc.process(job)
        except Exception:  # noqa: BLE001 - a single job must never kill the worker loop.
            LOGGER.exception(
                "Unhandled error processing job_id=%s worker=%s", job.id, worker_id
            )
