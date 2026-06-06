from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient


def _utcnow():
    return datetime.now(timezone.utc)

from app.core.models import GoogleToken as WorkerGoogleToken, Settings as WorkerSettings
from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.main import create_app
from tests.fakes import build_fake_repositories
from tests.support import FakeDriveClient, drive_file


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env({
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
    })


def _client(tmp_path, repos=None):
    repos = repos or build_fake_repositories()
    return TestClient(create_app(_settings(tmp_path), repositories=repos)), repos


def _app_with_worker(tmp_path, *, deepgram_key="dg-key", files=None):
    """Build the app wired to a worker (jobs) bundle and a fake Drive boundary.

    Login bootstraps the admin as user id=1 in the auth bundle; the worker bundle
    is seeded for that same id so run-once finds settings/token/Deepgram key.
    """
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    worker.settings.set(WorkerSettings(1, "src-folder-id", "dst-folder-id", False, deepgram_key))
    worker.google_tokens.set(1, WorkerGoogleToken(access_token="a", token_uri="u", client_id="c"))
    app = create_app(_settings(tmp_path), repositories=auth, worker_repositories=worker)
    drive = FakeDriveClient(files=list(files or []))
    app.state.build_drive_client = lambda credentials, src, dst: drive
    app.state.credentials_from_token = lambda token: object()
    return app, worker


def _login(client, username="admin", password="secret"):
    response = client.post("/login", data={"username": username, "password": password},
                           follow_redirects=False)
    assert response.status_code in {302, 303}, response.text
    return response


def test_health_ok(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        assert client.get("/health").json() == {"status": "ok"}


def test_dashboard_redirects_when_anonymous(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code in {302, 303, 307}
        assert r.headers["location"].startswith("/login")


def test_bootstrap_admin_login_sets_httponly_cookie(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        r = _login(client)
        assert r.headers["location"] == "/"
        assert "httponly" in r.headers["set-cookie"].lower()
        admin = repos.users.get_by_email("admin")
        assert admin.role == "admin"


def test_disabled_user_cannot_login(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        client.get("/health")  # ensure lifespan/bootstrap ran
        admin = repos.users.get_by_email("admin")
        repos.users.set_active(admin.id, False)
        r = client.post("/login", data={"username": "admin", "password": "secret"},
                        follow_redirects=False)
        assert r.status_code == 401


def test_wrong_password_rejected(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        r = client.post("/login", data={"username": "admin", "password": "nope"},
                        follow_redirects=False)
        assert r.status_code == 401


def test_settings_drive_save_extracts_folder_ids(tmp_path):
    client, repos = _client(tmp_path)
    folder = "1A2b3C4d5E6f7G8h9I0jKl"
    with client:
        _login(client)
        r = client.post("/settings/drive", data={
            "source_drive_folder_url": f"https://drive.google.com/drive/folders/{folder}?usp=sharing",
            "destination_drive_folder_url": "",
            "save_copy_to_drive": "true",
        }, follow_redirects=False)
        assert r.status_code == 303
        admin = repos.users.get_by_email("admin")
        saved = repos.drive_settings.get_for_user(admin.id)
        assert saved.source_drive_folder_id == folder
        assert saved.destination_drive_folder_id is None
        assert saved.save_copy_to_drive is True


def test_settings_drive_rejects_bad_url(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        r = client.post("/settings/drive", data={
            "source_drive_folder_url": "not a url", "destination_drive_folder_url": "",
        }, follow_redirects=False)
        assert r.status_code == 400


def test_deepgram_save_encrypts_and_masks(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client)
        client.post("/settings/deepgram", data={"deepgram_api_key": "dg-mysecretkey"},
                    follow_redirects=False)
        admin = repos.users.get_by_email("admin")
        assert repos.deepgram_credentials.get_encrypted_for_user(admin.id) != "dg-mysecretkey"
        page = client.get("/settings/deepgram").text
        assert "Configured" in page
        assert "dg-mysecretkey" not in page


def test_run_once_blocks_without_deepgram_key(tmp_path):
    # Settings + Google connected, but no per-user Deepgram key: nothing enqueues.
    app, worker = _app_with_worker(tmp_path, deepgram_key=None,
                                   files=[drive_file("file-1", "meet.mp4")])
    with TestClient(app) as client:
        _login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        page = client.get("/jobs").text
    assert worker.jobs.list_jobs_for_user(1) == []
    assert "Configure sua Deepgram API Key antes de iniciar uma transcrição." in page


def test_run_once_enqueues_pending_when_ready(tmp_path):
    # Web layer only creates a pending job (no download/transcribe/upload here).
    app, worker = _app_with_worker(tmp_path, deepgram_key="dg-key",
                                   files=[drive_file("file-1", "meet.mp4")])
    with TestClient(app) as client:
        _login(client)
        client.post("/jobs/run-once", follow_redirects=False)
    jobs = worker.jobs.list_jobs_for_user(1)
    assert len(jobs) == 1 and jobs[0].status == "pending"
    assert jobs[0].source_file_id == "file-1"


def test_automation_settings_save_and_render(tmp_path):
    app, worker = _app_with_worker(tmp_path)
    with TestClient(app) as client:
        _login(client)
        r = client.post("/settings/automation", data={
            "auto_poll_enabled": "true",
            "poll_interval_seconds": "120",
            "max_files_per_poll": "3",
        }, follow_redirects=False)
        assert r.status_code == 303
        saved = worker.automation.get_for_user(1)
        assert saved.auto_poll_enabled is True
        assert saved.poll_interval_seconds == 120
        assert saved.max_files_per_poll == 3
        page = client.get("/settings/automation").text
        assert "120" in page  # interval rendered


def test_check_now_creates_and_enqueues_jobs(tmp_path):
    from app.queue.memory_queue import InMemoryTranscriptionQueue
    app, worker = _app_with_worker(
        tmp_path, deepgram_key="dg-key", files=[drive_file("f1", "a.mp4"), drive_file("f2", "b.mp4")]
    )
    queue = InMemoryTranscriptionQueue()
    app.state.queue = queue
    with TestClient(app) as client:
        _login(client)
        r = client.post("/automation/check-now", follow_redirects=False)
        assert r.status_code == 303
    jobs = worker.jobs.list_jobs_for_user(1)
    assert len(jobs) == 2 and all(j.status == "pending" for j in jobs)
    assert len(queue.queued_job_ids()) == 2
    assert worker.automation.get_for_user(1).last_poll_at is not None


def test_check_now_survives_mark_poll_result_failure(tmp_path):
    # Bookkeeping errors must flash, never 500 (the route's stated contract).
    app, worker = _app_with_worker(
        tmp_path, deepgram_key="dg-key", files=[drive_file("f1", "a.mp4")]
    )

    def _boom(*a, **k):
        raise RuntimeError("db down")

    worker.automation.mark_poll_result = _boom
    with TestClient(app) as client:
        _login(client)
        r = client.post("/automation/check-now", follow_redirects=False)
        assert r.status_code == 303  # redirected with a flash, not a 500
    # The job was still created (the failure was only in status bookkeeping).
    assert len(worker.jobs.list_jobs_for_user(1)) == 1


def test_retry_failed_job_resets_and_reenqueues(tmp_path):
    from app.queue.memory_queue import InMemoryTranscriptionQueue
    app, worker = _app_with_worker(tmp_path)
    queue = InMemoryTranscriptionQueue()
    app.state.queue = queue
    # A failed, dead-lettered job owned by user 1.
    job = worker.jobs.create_job(1, "src-1", "a.mp4", _utcnow())
    worker.jobs.mark_failed(job.id, "boom", _utcnow(), error_code="UNEXPECTED")
    queue.mark_dead(job.id)
    with TestClient(app) as client:
        _login(client)
        r = client.post(f"/jobs/{job.id}/retry", follow_redirects=False)
        assert r.status_code == 303
    done = worker.jobs.get_job(job.id)
    assert done.status == "pending"
    assert done.attempts == 0
    assert job.id in queue.queued_job_ids()
    assert queue.dead_job_ids() == set()  # removed from the dead set


def test_retry_other_users_job_is_404(tmp_path):
    app, worker = _app_with_worker(tmp_path)
    other = worker.jobs.create_job(999, "src-x", "x.mp4", _utcnow())
    worker.jobs.mark_failed(other.id, "boom", _utcnow())
    with TestClient(app) as client:
        _login(client)
        r = client.post(f"/jobs/{other.id}/retry", follow_redirects=False)
        assert r.status_code == 404


def test_admin_queue_page_shows_stats(tmp_path):
    from app.queue.memory_queue import InMemoryTranscriptionQueue
    app, worker = _app_with_worker(tmp_path)
    queue = InMemoryTranscriptionQueue()
    queue.enqueue(1)
    queue.mark_dead(2)
    app.state.queue = queue
    with TestClient(app) as client:
        _login(client)
        page = client.get("/admin/queue")
        assert page.status_code == 200
        assert "Queue" in page.text or "Fila" in page.text


def test_connect_google_redirects_with_state(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        r = client.get("/connect-google", follow_redirects=False)
        assert "accounts.google.com" in r.headers["location"]
        assert "state=" in r.headers["location"]


def test_oauth_callback_saves_token_and_identity(tmp_path, monkeypatch):
    client, repos = _client(tmp_path)
    monkeypatch.setattr("app.web.main.exchange_google_code", lambda s, code: {
        "access_token": "access-token", "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token", "client_id": "client-id",
        "client_secret": "client-secret", "scopes": "https://www.googleapis.com/auth/drive",
        "expiry": "2026-06-03T00:00:00+00:00",
    })
    monkeypatch.setattr("app.web.main.fetch_google_userinfo",
                        lambda token: {"email": "me@gmail.com", "name": "Me"})
    with client:
        _login(client)
        connect = client.get("/connect-google", follow_redirects=False)
        state = parse_qs(urlparse(connect.headers["location"]).query)["state"][0]
        r = client.get(f"/oauth/google/callback?code=abc&state={state}", follow_redirects=False)
        assert r.status_code in {302, 303}
        admin = repos.users.get_by_email("admin")
        assert repos.google_tokens.get_for_user(admin.id) is not None
        assert repos.users.get_by_id(admin.id).google_email == "me@gmail.com"


def test_oauth_callback_rejects_bad_state(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        client.get("/connect-google", follow_redirects=False)
        r = client.get("/oauth/google/callback?code=abc&state=wrong")
        assert r.status_code == 400
