from datetime import datetime, timedelta, timezone

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


def test_requeue_with_now_skips_jobs_in_backoff():
    repos = build_memory_repositories()
    due = repos.jobs.create_job(7, "src-due", "d.mp4", _now())
    waiting = repos.jobs.create_job(7, "src-wait", "w.mp4", _now())
    repos.jobs.schedule_retry(
        waiting.id, _now(), next_retry_at=_now() + timedelta(minutes=5),
        error_code="RATE_LIMIT", error_message="x",
    )
    queue = InMemoryTranscriptionQueue()

    count = requeue_pending_jobs(repos, queue, now=_now())

    assert count == 1
    assert queue.queued_job_ids() == {due.id}  # backoff job not woken early


def test_requeue_without_now_enqueues_all_pending_back_compat():
    repos = build_memory_repositories()
    a = repos.jobs.create_job(7, "src-a", "a.mp4", _now())
    b = repos.jobs.create_job(7, "src-b", "b.mp4", _now())
    repos.jobs.schedule_retry(
        b.id, _now(), next_retry_at=_now() + timedelta(minutes=5),
        error_code="RATE_LIMIT", error_message="x",
    )
    queue = InMemoryTranscriptionQueue()

    assert requeue_pending_jobs(repos, queue) == 2  # no `now` -> ignore backoff
    assert queue.queued_job_ids() == {a.id, b.id}


def test_build_queue_factory_selects_backend():
    assert build_queue(QueueSettings.from_env({})) is None  # "none" -> poll mode
    mem = build_queue(QueueSettings.from_env({"QUEUE_BACKEND": "memory"}))
    assert isinstance(mem, InMemoryTranscriptionQueue)
