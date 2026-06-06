import threading
from datetime import datetime, timezone

from app.core.models import JobStatus
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.worker.processor import ResolvedProvider
from app.worker.queue_loop import run_queue_loop
from tests.support import make_worker_container


def _now():
    return datetime.now(timezone.utc)


def _resolved(kind):
    return ResolvedProvider(
        provider=object(), name=kind, kind=kind, status=None, settings=None, token=None,
    )


class FakeProc:
    """A processor stub that resolves a fixed kind and records processing.

    ``kinds`` maps job_id -> 'cloud'/'local'. ``raise_on`` ids raise during process.
    """

    def __init__(self, repos, default_kind="cloud", kinds=None, raise_on=None, on_process=None):
        self.repos = repos
        self.default_kind = default_kind
        self.kinds = kinds or {}
        self.raise_on = set(raise_on or [])
        self.on_process = on_process
        self.processed = []

    def resolve(self, job):
        return _resolved(self.kinds.get(job.id, self.default_kind))

    def process(self, job, resolved=None):
        self.processed.append(job.id)
        if self.on_process is not None:
            self.on_process(job)
        if job.id in self.raise_on:
            raise RuntimeError("boom")
        self.repos.jobs.mark_completed(job.id, _now())


def test_processes_enqueued_jobs_and_clears_processing(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    j1 = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    j2 = container.repositories.jobs.create_job(7, "s2", "b.mp4", _now())
    queue.enqueue(j1.id)
    queue.enqueue(j2.id)
    proc = FakeProc(container.repositories)
    stop = threading.Event()

    run_queue_loop(container, stop, "w1", processor=proc, dequeue_timeout=0, on_idle=stop.set)

    assert proc.processed == [j1.id, j2.id]
    assert container.repositories.jobs.get_job(j1.id).status == JobStatus.COMPLETED.value
    assert queue.queued_job_ids() == set()
    assert queue.queue_stats()["processing"] == 0  # cleared after each job


def test_cloud_and_local_jobs_each_take_their_slot(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    cloud_job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    local_job = container.repositories.jobs.create_job(7, "s2", "b.mp4", _now())
    queue.enqueue(cloud_job.id)
    queue.enqueue(local_job.id)
    proc = FakeProc(
        container.repositories,
        kinds={cloud_job.id: "cloud", local_job.id: "local"},
    )
    stop = threading.Event()

    run_queue_loop(container, stop, "w1", processor=proc, dequeue_timeout=0, on_idle=stop.set)

    assert set(proc.processed) == {cloud_job.id, local_job.id}
    # Both slot kinds are free again after the run.
    assert queue.acquire_provider_slot("cloud", 60) is not None
    assert queue.acquire_provider_slot("local", 60) is not None


def test_requeues_when_no_slot_is_available(tmp_path):
    # cloud capacity 1, already taken -> the dequeued cloud job must be put back.
    queue = InMemoryTranscriptionQueue(cloud_concurrency=1)
    queue.acquire_provider_slot("cloud", 60)  # fill the only cloud slot elsewhere
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    proc = FakeProc(container.repositories, default_kind="cloud")
    stop = threading.Event()

    run_queue_loop(container, stop, "w1", processor=proc, dequeue_timeout=0, on_contention=stop.set)

    assert proc.processed == []                       # never ran without a slot
    assert queue.queued_job_ids() == {job.id}         # requeued for later
    assert container.repositories.jobs.get_job(job.id).status == JobStatus.PENDING.value  # not failed


def test_skips_job_that_is_no_longer_pending(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    container.repositories.jobs.claim_job(job.id, "other", _now())  # already processing
    queue.enqueue(job.id)
    proc = FakeProc(container.repositories)
    stop = threading.Event()

    run_queue_loop(container, stop, "w1", processor=proc, dequeue_timeout=0, on_idle=stop.set)

    assert proc.processed == []


def test_survives_a_transient_dequeue_error(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    proc = FakeProc(container.repositories)
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


def test_releases_slot_even_when_processing_raises(tmp_path):
    queue = InMemoryTranscriptionQueue(cloud_concurrency=1)
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    proc = FakeProc(container.repositories, raise_on={job.id})
    stop = threading.Event()

    run_queue_loop(container, stop, "w1", processor=proc, dequeue_timeout=0, on_idle=stop.set)

    # The slot must be free again and the processing set cleared despite the error.
    assert queue.acquire_provider_slot("cloud", 60) is not None
    assert queue.queue_stats()["processing"] == 0


def test_terminal_resolution_error_dead_letters_without_a_slot(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, queue=queue)
    job = container.repositories.jobs.create_job(7, "s1", "a.mp4", _now())
    queue.enqueue(job.id)
    stop = threading.Event()

    class _ResolveFails:
        def resolve(self, job):
            from app.errors import LocalTranscriptionUnavailableError
            raise LocalTranscriptionUnavailableError("no provider")

        def process(self, job, resolved=None):
            # Real processor re-resolves and dead-letters; emulate the terminal path.
            container.repositories.jobs.mark_failed(job.id, "no provider", _now(), error_code="CONFIG")
            queue.mark_dead(job.id)

    run_queue_loop(container, stop, "w1", processor=_ResolveFails(), dequeue_timeout=0, on_idle=stop.set)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert queue.dead_job_ids() == {job.id}
