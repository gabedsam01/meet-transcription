from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

from app.logger import setup_logging
from app.queue import requeue_pending_jobs
from app.worker.container import WorkerContainer, build_container
from app.worker.loop import run_worker_loop
from app.worker.queue_loop import run_queue_loop

LOGGER = logging.getLogger(__name__)


def recover_stale_jobs(container: WorkerContainer, now: datetime) -> int:
    stale_before = now - timedelta(minutes=container.settings.stale_job_timeout_minutes)
    reset = container.repositories.jobs.reset_stale_processing_jobs(stale_before, now)
    if reset:
        LOGGER.warning("Recovered %s stale processing job(s) to failed", len(reset))
    return len(reset)


def run(container: WorkerContainer, stop_event: threading.Event) -> None:
    recover_stale_jobs(container, datetime.now(timezone.utc))
    # Queue mode (QUEUE_BACKEND=redis|memory): re-enqueue any pending Postgres jobs
    # the queue may have lost, then consume the queue. Otherwise keep the legacy
    # poll loop that claims the next pending job directly from Postgres.
    if container.queue is not None:
        enqueued = requeue_pending_jobs(container.repositories, container.queue)
        LOGGER.info("Queue mode: reconciled %s pending job(s) at startup", enqueued)
        loop = run_queue_loop
    else:
        loop = run_worker_loop
    threads: list[threading.Thread] = []
    for i in range(container.settings.concurrency):
        worker_id = f"worker-{i + 1}"
        thread = threading.Thread(
            target=loop,
            args=(container, stop_event, worker_id),
            name=worker_id,
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


def main() -> int:
    setup_logging()
    container = build_container()
    stop_event = threading.Event()

    def _handle_signal(_signum, _frame):
        # Keep the handler async-signal-safe: only set the event. Logging acquires a
        # lock and is not safe to call from a signal handler.
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    LOGGER.info(
        "Worker starting backend=%s concurrency=%s",
        container.settings.repository_backend,
        container.settings.concurrency,
    )
    run(container, stop_event)
    LOGGER.info("Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
