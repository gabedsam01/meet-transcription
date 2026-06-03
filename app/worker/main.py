from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

from app.logger import setup_logging
from app.worker.container import WorkerContainer, build_container
from app.worker.loop import run_worker_loop

LOGGER = logging.getLogger(__name__)


def recover_stale_jobs(container: WorkerContainer, now: datetime) -> int:
    stale_before = now - timedelta(minutes=container.settings.stale_job_timeout_minutes)
    reset = container.repositories.jobs.reset_stale_processing_jobs(stale_before, now)
    if reset:
        LOGGER.warning("Recovered %s stale processing job(s) to failed", len(reset))
    return len(reset)


def run(container: WorkerContainer, stop_event: threading.Event) -> None:
    recover_stale_jobs(container, datetime.now(timezone.utc))
    threads: list[threading.Thread] = []
    for i in range(container.settings.concurrency):
        worker_id = f"worker-{i + 1}"
        thread = threading.Thread(
            target=run_worker_loop,
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

    def _handle_signal(signum, _frame):
        LOGGER.info("Received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    LOGGER.info(
        "Worker starting backend=%s concurrency=%s",
        container.settings.repository_backend,
        container.settings.concurrency,
    )
    run(container, stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
