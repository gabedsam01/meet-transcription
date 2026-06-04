import threading
from datetime import datetime, timezone

from app.core.models import JobStatus
from app.worker.loop import run_worker_loop
from tests.support import make_worker_container


def _now():
    return datetime.now(timezone.utc)


class RecordingProcessor:
    def __init__(self, repositories):
        self.repositories = repositories
        self.processed = []

    def process(self, job):
        self.processed.append(job.id)
        self.repositories.jobs.mark_completed(job.id, _now())


def test_loop_claims_and_processes_then_stops_when_idle(tmp_path):
    container = make_worker_container(tmp_path)
    job = container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    processor = RecordingProcessor(container.repositories)
    stop_event = threading.Event()

    # When the queue is empty the loop "sleeps"; use that hook to stop the loop.
    def fake_sleep(_seconds):
        stop_event.set()

    run_worker_loop(container, stop_event, "w1", processor=processor, sleep=fake_sleep)

    assert processor.processed == [job.id]
    assert container.repositories.jobs.get_job(job.id).status == JobStatus.COMPLETED.value


def test_loop_exits_immediately_when_already_stopped(tmp_path):
    container = make_worker_container(tmp_path)
    container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    processor = RecordingProcessor(container.repositories)
    stop_event = threading.Event()
    stop_event.set()

    run_worker_loop(container, stop_event, "w1", processor=processor, sleep=lambda s: None)

    assert processor.processed == []  # never claimed because stop was set first


def test_loop_survives_a_job_whose_processing_raises(tmp_path):
    container = make_worker_container(tmp_path)
    container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    stop_event = threading.Event()

    class BoomProcessor:
        def process(self, job):
            raise RuntimeError("boom")

    # Once the raising job is handled the queue is empty -> sleeper stops the loop.
    def fake_sleep(_seconds):
        stop_event.set()

    # Must NOT propagate: a single failing job cannot kill the worker loop.
    run_worker_loop(container, stop_event, "w1", processor=BoomProcessor(), sleep=fake_sleep)
