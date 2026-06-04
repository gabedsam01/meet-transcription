import threading
from datetime import datetime, timezone

from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.worker.main import run
from tests.support import make_worker_container


def _now():
    return datetime.now(timezone.utc)


def test_run_in_queue_mode_reconciles_pending_jobs_at_startup(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    pending = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    stop = threading.Event()
    stop.set()  # threads exit immediately; run() must not hang

    run(container, stop)

    # Even though the loop never ran, startup reconciliation re-enqueued the
    # pending Postgres job into Redis (the self-heal path).
    assert queue.queued_job_ids() == {pending.id}
