from datetime import datetime, timezone

from app.queue import build_queue, requeue_pending_jobs
from app.queue.config import QueueSettings
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.repositories.memory import build_memory_repositories


def _now():
    return datetime.now(timezone.utc)


def test_requeue_enqueues_only_pending_jobs():
    repos = build_memory_repositories()
    a = repos.jobs.create_job(7, "src-a", "a.mp4", _now())
    b = repos.jobs.create_job(7, "src-b", "b.mp4", _now())
    repos.jobs.claim_job(a.id, "w", _now())  # a -> processing, not pending anymore
    queue = InMemoryTranscriptionQueue()

    count = requeue_pending_jobs(repos, queue)

    assert count == 1
    assert queue.queued_job_ids() == {b.id}


def test_requeue_is_idempotent_and_creates_no_duplicates():
    repos = build_memory_repositories()
    b = repos.jobs.create_job(7, "src-b", "b.mp4", _now())
    queue = InMemoryTranscriptionQueue()

    assert requeue_pending_jobs(repos, queue) == 1
    assert requeue_pending_jobs(repos, queue) == 0  # already queued -> deduped
    assert queue.queued_job_ids() == {b.id}


def test_build_queue_factory_selects_backend():
    assert build_queue(QueueSettings.from_env({})) is None  # "none" -> poll mode
    mem = build_queue(QueueSettings.from_env({"QUEUE_BACKEND": "memory"}))
    assert isinstance(mem, InMemoryTranscriptionQueue)
