import threading
from datetime import datetime, timezone

from app.core.models import JobStatus
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.worker.queue_loop import run_queue_loop
from tests.support import make_worker_container


def _now():
    return datetime.now(timezone.utc)


class RecordingProcessor:
    def __init__(self, repos):
        self.repos = repos
        self.processed = []

    def process(self, job):
        self.processed.append(job.id)
        self.repos.jobs.mark_completed(job.id, _now())


def test_processes_enqueued_jobs_one_at_a_time(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    j1 = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    j2 = container.repositories.jobs.create_job(7, "s2", "b.mp4", _now())
    queue.enqueue(j1.id)
    queue.enqueue(j2.id)
    proc = RecordingProcessor(container.repositories)
    stop = threading.Event()

    run_queue_loop(
        container, stop, "w1", processor=proc, dequeue_timeout=0, on_idle=stop.set
    )

    assert proc.processed == [j1.id, j2.id]
    assert container.repositories.jobs.get_job(j1.id).status == JobStatus.COMPLETED.value
    assert queue.queued_job_ids() == set()


def test_holds_global_lock_during_processing_and_releases_after(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    observed = {}

    class _P:
        def __init__(self, repos):
            self.repos = repos

        def process(self, j):
            # A second acquire must fail while this job is being processed.
            observed["locked_during"] = queue.acquire_global_lock(60) is None
            self.repos.jobs.mark_completed(j.id, _now())

    stop = threading.Event()
    run_queue_loop(
        container, stop, "w1", processor=_P(container.repositories),
        dequeue_timeout=0, on_idle=stop.set,
    )

    assert observed["locked_during"] is True
    assert queue.acquire_global_lock(60) is not None  # released after processing


def test_requeues_job_when_lock_is_held_elsewhere(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    queue.acquire_global_lock(60)  # another worker already holds the single lock
    proc = RecordingProcessor(container.repositories)
    stop = threading.Event()

    run_queue_loop(
        container, stop, "w1", processor=proc, dequeue_timeout=0, on_contention=stop.set
    )

    assert proc.processed == []  # never ran while the lock was held
    assert queue.queued_job_ids() == {job.id}  # put back for a later retry


def test_skips_job_that_is_no_longer_pending(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    container.repositories.jobs.claim_job(job.id, "other-worker", _now())  # already taken
    queue.enqueue(job.id)
    proc = RecordingProcessor(container.repositories)
    stop = threading.Event()

    run_queue_loop(
        container, stop, "w1", processor=proc, dequeue_timeout=0, on_idle=stop.set
    )

    assert proc.processed == []  # claim_job returned None -> dedupe defense


def test_survives_a_transient_queue_error(tmp_path):
    # A dequeue that raises once (e.g. a Redis hiccup) must NOT kill the worker
    # thread — the loop logs, backs off, and recovers on the next iteration.
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    proc = RecordingProcessor(container.repositories)
    stop = threading.Event()
    real_dequeue = queue.dequeue
    state = {"n": 0}

    def flaky_dequeue(timeout=0):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("redis hiccup")
        return real_dequeue(timeout)

    queue.dequeue = flaky_dequeue
    run_queue_loop(
        container, stop, "w1", processor=proc, dequeue_timeout=0,
        on_idle=stop.set, on_error=lambda: None,
    )
    assert proc.processed == [job.id]  # recovered after the transient error


def test_survives_a_job_whose_processing_raises(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    stop = threading.Event()

    class _Boom:
        def process(self, j):
            raise RuntimeError("boom")

    # Must not propagate and must release the lock so the next idle can stop us.
    run_queue_loop(
        container, stop, "w1", processor=_Boom(), dequeue_timeout=0, on_idle=stop.set
    )
    assert queue.acquire_global_lock(60) is not None  # lock released despite the error
