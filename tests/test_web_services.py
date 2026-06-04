from app.web import services
from app.web.deepgram_key import DeepgramKeyStore
from app.web.repositories import DriveSettings, GoogleToken
from app.web.security import fernet_from_secret
from tests.fakes import build_fake_repositories


def _deepgram(repos):
    return DeepgramKeyStore(repos.deepgram_credentials, fernet_from_secret("a-long-secret-for-tests"))


def _connect_google(repos, user_id):
    repos.google_tokens.save_for_user(user_id, GoogleToken("a", "r", "u", "c", "s", "sc", None))


def _set_source(repos, user_id):
    repos.drive_settings.save_for_user(
        user_id, DriveSettings("url", "source-id", None, None, False)
    )


def test_enqueue_reports_missing_settings():
    repos = build_fake_repositories()
    result = services.enqueue_run_once_job(repos, _deepgram(repos), 1)
    assert result.status == "missing_settings"
    assert repos.jobs.list_jobs_for_user(1) == []


def test_enqueue_reports_google_not_connected():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    result = services.enqueue_run_once_job(repos, _deepgram(repos), 1)
    assert result.status == "not_connected"


def test_enqueue_blocks_without_deepgram_key():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    _connect_google(repos, 1)
    result = services.enqueue_run_once_job(repos, _deepgram(repos), 1)
    assert result.status == "no_deepgram_key"
    assert repos.jobs.list_jobs_for_user(1) == []


def test_enqueue_creates_pending_job_when_ready():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    _connect_google(repos, 1)
    deepgram = _deepgram(repos)
    deepgram.save_for_user(1, "dg-key")
    result = services.enqueue_run_once_job(repos, deepgram, 1)
    assert result.status == "created"
    assert result.job.status == "pending"
    assert [j.status for j in repos.jobs.list_jobs_for_user(1)] == ["pending"]


def test_enqueue_blocks_when_active_job_exists():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    _connect_google(repos, 1)
    deepgram = _deepgram(repos)
    deepgram.save_for_user(1, "dg-key")
    repos.jobs.create_job(user_id=1, status="processing")
    result = services.enqueue_run_once_job(repos, deepgram, 1)
    assert result.status == "already_running"
    assert len(repos.jobs.list_jobs_for_user(1)) == 1
