from datetime import datetime, timezone

from app.core.models import GoogleToken, Settings
from app.processor import DriveFile
from app.repositories.memory import build_memory_repositories
from app.services.drive_watcher import poll_user
from app.services.guardrails import Guardrails, resolve_guardrails
from tests.support import FakeDriveClient


def _now():
    return datetime.now(timezone.utc)


def _media(file_id, size_bytes):
    return DriveFile(
        id=file_id, name=f"{file_id}.mp4", mime_type="video/mp4", size=size_bytes,
        created_time="2026-06-05T10:00:00Z", modified_time="2026-06-05T10:00:00Z",
    )


def _seed(repos, user_id=7):
    repos.settings.set(Settings(user_id, "src", "dst", False, "dg"))
    repos.google_tokens.set(user_id, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def _poll(repos, drive, guardrails, max_files=5):
    return poll_user(
        repos,
        build_drive_client=lambda creds, src, dst: drive,
        credentials_from_token=lambda token: object(),
        user_id=7, now=_now(), max_files=max_files, guardrails=guardrails,
    )


def test_oversized_file_is_skipped_with_friendly_message():
    repos = build_memory_repositories()
    _seed(repos)
    big = _media("big", 2 * 1024 * 1024)   # 2 MB
    small = _media("small", 10)
    drive = FakeDriveClient(files=[big, small])

    result = _poll(repos, drive, Guardrails(max_file_size_mb=1))

    assert result.created == 1   # only the small file
    assert result.skipped == 1
    assert "excede" in (result.error_message or "")


def test_daily_limit_stops_creating_more_jobs():
    repos = build_memory_repositories()
    _seed(repos)
    drive = FakeDriveClient(files=[_media(f"f{i}", 10) for i in range(5)])

    result = _poll(repos, drive, Guardrails(daily_jobs_limit=2))

    assert result.created == 2
    assert "Limite diário" in (result.error_message or "")


def test_daily_limit_counts_jobs_already_created_today():
    repos = build_memory_repositories()
    _seed(repos)
    # Two jobs already created today exhaust a limit of 2.
    repos.jobs.create_job(7, "old1", "old1.mp4", _now())
    repos.jobs.create_job(7, "old2", "old2.mp4", _now())
    drive = FakeDriveClient(files=[_media("new", 10)])

    result = _poll(repos, drive, Guardrails(daily_jobs_limit=2))

    assert result.created == 0


def test_no_limits_allows_everything():
    repos = build_memory_repositories()
    _seed(repos)
    drive = FakeDriveClient(files=[_media("a", 99 * 1024 * 1024), _media("b", 10)])
    result = _poll(repos, drive, Guardrails())  # all None -> unlimited
    assert result.created == 2


def test_resolve_guardrails_prefers_user_override_else_global_default():
    # Per-user override wins.
    over = resolve_guardrails(
        _AutoStub(max_file_size_mb=50, daily_jobs_limit=3),
        default_max_file_size_mb=100, default_daily_jobs_limit=10,
    )
    assert over.max_file_size_mb == 50 and over.daily_jobs_limit == 3
    # Falls back to the global default when the user leaves it NULL.
    fallback = resolve_guardrails(
        _AutoStub(max_file_size_mb=None, daily_jobs_limit=None),
        default_max_file_size_mb=100, default_daily_jobs_limit=10,
    )
    assert fallback.max_file_size_mb == 100 and fallback.daily_jobs_limit == 10
    # No automation row at all -> globals.
    none = resolve_guardrails(
        None, default_max_file_size_mb=100, default_daily_jobs_limit=10
    )
    assert none.max_file_size_mb == 100 and none.daily_jobs_limit == 10


class _AutoStub:
    def __init__(self, max_file_size_mb, daily_jobs_limit):
        self.max_file_size_mb = max_file_size_mb
        self.daily_jobs_limit = daily_jobs_limit
