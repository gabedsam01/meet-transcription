import threading
from datetime import datetime, timedelta, timezone

from app.core.models import JobStatus
from app.worker.main import recover_stale_jobs, run
from tests.support import make_worker_container


def _now():
    return datetime.now(timezone.utc)


def test_recover_stale_jobs_marks_old_processing_failed(tmp_path):
    container = make_worker_container(tmp_path)
    job = container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    container.repositories.jobs.claim_next_pending_job("w", _now())
    container.repositories.jobs._jobs[job.id].started_at = _now() - timedelta(hours=5)

    count = recover_stale_jobs(container, _now())

    assert count == 1
    assert container.repositories.jobs.get_job(job.id).status == JobStatus.FAILED.value


def test_run_recovers_stale_then_exits_when_stop_is_preset(tmp_path):
    container = make_worker_container(tmp_path)
    job = container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    container.repositories.jobs.claim_next_pending_job("w", _now())
    container.repositories.jobs._jobs[job.id].started_at = _now() - timedelta(hours=5)
    stop_event = threading.Event()
    stop_event.set()  # threads exit immediately; run() must not hang

    run(container, stop_event)

    assert container.repositories.jobs.get_job(job.id).status == JobStatus.FAILED.value
