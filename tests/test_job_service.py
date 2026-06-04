from datetime import datetime, timezone

from app.core.models import GoogleToken, JobStatus, Settings
from app.repositories.memory import build_memory_repositories
from app.services.job_service import create_next_pending_job
from tests.support import FakeDriveClient, drive_file


def _now():
    return datetime.now(timezone.utc)


def _build(files, *, with_settings=True, with_token=True):
    repos = build_memory_repositories()
    if with_settings:
        repos.settings.set(Settings(7, "src", "dst", False, "dg"))
    if with_token:
        repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))
    drive = FakeDriveClient(files=files)
    return repos, drive


def _call(repos, drive):
    return create_next_pending_job(
        repos,
        build_drive_client=lambda credentials, src, dst: drive,
        credentials_from_token=lambda token: object(),
        user_id=7,
    )


def test_reports_no_settings():
    repos = build_memory_repositories()
    result = _call(repos, FakeDriveClient())
    assert result.status == "no_settings"


def test_reports_not_connected():
    repos = build_memory_repositories()
    repos.settings.set(Settings(7, "src", "dst", False, "dg"))
    result = _call(repos, FakeDriveClient())
    assert result.status == "not_connected"


def test_reports_no_deepgram_key():
    # Settings + Google connected, but no per-user Deepgram key: must not enqueue.
    repos = build_memory_repositories()
    repos.settings.set(Settings(7, "src", "dst", False, None))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))
    result = _call(repos, FakeDriveClient(files=[drive_file("file-1", "a.mp4")]))
    assert result.status == "no_deepgram_key"
    assert result.job is None
    assert repos.jobs.list_jobs_for_user(7) == []


def test_creates_pending_job_for_first_new_video():
    repos, drive = _build([drive_file("file-1", "a.mp4"), drive_file("file-2", "b.mp4")])
    result = _call(repos, drive)
    assert result.status == "created"
    assert result.job.status == JobStatus.PENDING.value
    assert result.job.source_file_id == "file-1"
    assert result.job.source_file_name == "a.mp4"


def test_skips_already_completed_video():
    repos, drive = _build([drive_file("file-1", "a.mp4"), drive_file("file-2", "b.mp4")])
    done = repos.jobs.create_job(7, "file-1", "a.mp4", _now())
    repos.jobs.mark_completed(done.id, _now())
    result = _call(repos, drive)
    assert result.status == "created"
    assert result.job.source_file_id == "file-2"


def test_skips_video_with_active_job():
    repos, drive = _build([drive_file("file-1", "a.mp4"), drive_file("file-2", "b.mp4")])
    repos.jobs.create_job(7, "file-1", "a.mp4", _now())  # pending
    result = _call(repos, drive)
    assert result.job.source_file_id == "file-2"


def test_reports_no_new_videos_when_all_taken():
    repos, drive = _build([drive_file("file-1", "a.mp4")])
    repos.jobs.create_job(7, "file-1", "a.mp4", _now())  # pending blocks it
    result = _call(repos, drive)
    assert result.status == "no_new_videos"
    assert result.job is None


def test_does_not_duplicate_when_run_twice():
    repos, drive = _build([drive_file("file-1", "a.mp4")])
    first = _call(repos, drive)
    second = _call(repos, drive)
    assert first.status == "created"
    assert second.status == "no_new_videos"
    assert len(repos.jobs.list_jobs_for_user(7)) == 1
