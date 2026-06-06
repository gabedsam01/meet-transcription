from datetime import datetime, timezone

from app.core.models import GoogleToken, Settings
from app.repositories.memory import build_memory_repositories
from app.services.drive_watcher import poll_user
from tests.support import FakeDriveClient, drive_file


def _now():
    return datetime.now(timezone.utc)


def _seed(repos, user_id=7, deepgram_key="dg"):
    repos.settings.set(Settings(user_id, "src-folder", "dst-folder", False, deepgram_key))
    repos.google_tokens.set(user_id, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def _poll(repos, drive, user_id=7, max_files=5, guardrails=None):
    return poll_user(
        repos,
        build_drive_client=lambda creds, src, dst: drive,
        credentials_from_token=lambda token: object(),
        user_id=user_id,
        now=_now(),
        max_files=max_files,
        guardrails=guardrails,
    )


def test_poll_creates_pending_jobs_for_each_new_file():
    repos = build_memory_repositories()
    _seed(repos)
    drive = FakeDriveClient(files=[drive_file("f1"), drive_file("f2"), drive_file("f3")])

    result = _poll(repos, drive)

    assert result.created == 3
    assert result.error_code is None
    assert len(result.job_ids) == 3
    statuses = {j.status for j in repos.jobs.list_jobs_for_user(7)}
    assert statuses == {"pending"}


def test_poll_dedupes_files_with_an_existing_job():
    repos = build_memory_repositories()
    _seed(repos)
    # f1 already has a completed job; f2 is new.
    existing = repos.jobs.create_job(7, "f1", "f1.mp4", _now())
    repos.jobs.mark_completed(existing.id, _now())
    drive = FakeDriveClient(files=[drive_file("f1"), drive_file("f2")])

    result = _poll(repos, drive)

    assert result.created == 1
    assert result.skipped == 1


def test_poll_respects_max_files():
    repos = build_memory_repositories()
    _seed(repos)
    drive = FakeDriveClient(files=[drive_file(f"f{i}") for i in range(5)])

    result = _poll(repos, drive, max_files=2)

    assert result.created == 2


def test_poll_reports_friendly_error_when_drive_list_fails():
    repos = build_memory_repositories()
    _seed(repos)
    drive = FakeDriveClient(fail_list=True)

    result = _poll(repos, drive)

    assert result.created == 0
    assert result.error_code == "DRIVE_ERROR"
    assert result.error_message  # friendly, non-empty
    assert "Traceback" not in (result.error_message or "")  # no stack trace leak


def test_poll_requires_settings_and_token():
    repos = build_memory_repositories()  # nothing seeded
    drive = FakeDriveClient(files=[drive_file("f1")])
    result = _poll(repos, drive)
    assert result.error_code == "NO_SETTINGS"

    repos2 = build_memory_repositories()
    repos2.settings.set(Settings(7, "src", "dst", False, "dg"))  # token missing
    result2 = _poll(repos2, drive)
    assert result2.error_code == "NOT_CONNECTED"
