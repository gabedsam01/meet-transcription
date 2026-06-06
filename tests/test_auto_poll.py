from datetime import datetime, timedelta, timezone

from app.core.models import GoogleToken, Settings
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.worker.auto_poll import auto_poll_tick
from tests.support import FakeDriveClient, drive_file, make_worker_container


def _now():
    return datetime.now(timezone.utc)


def _container(tmp_path, drive, user_id=7, enabled=True, deepgram_key="dg"):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(tmp_path, drive=drive, queue=queue)
    repos = container.repositories
    repos.settings.set(Settings(user_id, "src", "dst", False, deepgram_key))
    repos.google_tokens.set(user_id, GoogleToken(access_token="a", token_uri="u", client_id="c"))
    repos.automation.upsert_for_user(user_id, auto_poll_enabled=enabled, poll_interval_seconds=300)
    return container, queue


def test_tick_creates_and_enqueues_jobs_and_marks_success(tmp_path):
    drive = FakeDriveClient(files=[drive_file("f1"), drive_file("f2")])
    container, queue = _container(tmp_path, drive)

    created = auto_poll_tick(container, now=_now)

    assert created == 2
    assert len(queue.queued_job_ids()) == 2
    auto = container.repositories.automation.get_for_user(7)
    assert auto.last_poll_at is not None and auto.last_success_at is not None
    assert auto.last_error_code is None


def test_tick_does_not_duplicate_existing_jobs(tmp_path):
    drive = FakeDriveClient(files=[drive_file("f1"), drive_file("f2")])
    container, queue = _container(tmp_path, drive)
    existing = container.repositories.jobs.create_job(7, "f1", "f1.mp4", _now())
    container.repositories.jobs.mark_completed(existing.id, _now())

    created = auto_poll_tick(container, now=_now)

    assert created == 1  # only f2


def test_tick_skips_when_another_poller_holds_the_lock(tmp_path):
    drive = FakeDriveClient(files=[drive_file("f1")])
    container, queue = _container(tmp_path, drive)
    queue.acquire_named_lock("lock:auto_poll", 60)  # someone else is polling

    created = auto_poll_tick(container, now=_now)

    assert created == 0
    assert queue.queued_job_ids() == set()


def test_tick_releases_lock_so_the_next_tick_can_run(tmp_path):
    drive = FakeDriveClient(files=[drive_file("f1")])
    container, queue = _container(tmp_path, drive)
    auto_poll_tick(container, now=_now)
    # Lock is free again after the tick.
    assert queue.acquire_named_lock("lock:auto_poll", 60) is not None


def test_tick_records_friendly_error_on_drive_failure(tmp_path):
    drive = FakeDriveClient(fail_list=True)
    container, queue = _container(tmp_path, drive)

    created = auto_poll_tick(container, now=_now)

    assert created == 0
    auto = container.repositories.automation.get_for_user(7)
    assert auto.last_error_code == "DRIVE_ERROR"
    assert auto.last_error_message and "Traceback" not in auto.last_error_message


def test_tick_sweeps_due_retry_jobs_onto_the_queue(tmp_path):
    drive = FakeDriveClient(files=[])
    container, queue = _container(tmp_path, drive)
    # A pending job whose retry gate has already passed must be re-enqueued.
    job = container.repositories.jobs.create_job(7, "retry-src", "r.mp4", _now())
    container.repositories.jobs.schedule_retry(
        job.id, _now(), next_retry_at=_now() - timedelta(seconds=1),
        error_code="RATE_LIMIT", error_message="x",
    )

    auto_poll_tick(container, now=_now)

    assert job.id in queue.queued_job_ids()


def test_tick_does_nothing_when_user_disabled(tmp_path):
    drive = FakeDriveClient(files=[drive_file("f1")])
    container, queue = _container(tmp_path, drive, enabled=False)

    created = auto_poll_tick(container, now=_now)

    assert created == 0
    assert queue.queued_job_ids() == set()
