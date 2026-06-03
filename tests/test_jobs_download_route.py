from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.models import GoogleToken, Settings
from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.main import create_app
from tests.support import FakeDriveClient, drive_file


def _now():
    return datetime.now(timezone.utc)


def _web_settings(tmp_path) -> WebSettings:
    return WebSettings.from_env(
        {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "secret",
            "APP_SECRET_KEY": "a-long-secret-for-tests",
            "SESSION_COOKIE_SECURE": "false",
            "GOOGLE_WEB_CLIENT_ID": "client-id",
            "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
            "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
            "DATABASE_URL": str(tmp_path / "app.db"),
            "DEEPGRAM_API_KEY": "dg-key",
            "TMP_DIR": str(tmp_path / "tmp"),
        }
    )


def _login(client):
    assert client.post(
        "/login", data={"username": "admin", "password": "secret"}, follow_redirects=False
    ).status_code in {302, 303}


def _seed_user1(repos):
    # Admin login creates user id=1 in SQLite; seed the PG-side ports for that id.
    repos.settings.set(Settings(1, "src", "dst", False, "user-dg-key"))
    repos.google_tokens.set(1, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def test_run_once_creates_pending_job_without_processing(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    drive = FakeDriveClient(files=[drive_file("file-1", "meet.mp4")])
    app = create_app(_web_settings(tmp_path), repositories=repos)
    # Patch the drive factory used by the route so no real Google call happens.
    app.state.build_drive_client = lambda credentials, src, dst: drive
    app.state.credentials_from_token = lambda token: object()

    with TestClient(app) as client:
        _login(client)
        response = client.post("/jobs/run-once", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"

    jobs = repos.jobs.list_jobs_for_user(1)
    assert len(jobs) == 1
    assert jobs[0].status == "pending"
    assert jobs[0].source_file_id == "file-1"


def test_run_once_without_settings_redirects_with_message(tmp_path):
    repos = build_memory_repositories()  # no settings seeded
    app = create_app(_web_settings(tmp_path), repositories=repos)
    with TestClient(app) as client:
        _login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        page = client.get("/jobs")
    assert "Configure source and destination folders" in page.text
    assert repos.jobs.list_jobs_for_user(1) == []


def test_download_returns_owner_transcript(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    job = repos.jobs.create_job(1, "file-1", "Weekly Sync.mp4", _now())
    repos.transcripts.create(job.id, 1, "the transcript body", {"k": "v"}, None, _now())
    repos.jobs.mark_completed(job.id, _now())
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)
        response = client.get(f"/jobs/{job.id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Weekly_Sync_Transcricao.txt" in response.headers["content-disposition"]
    assert response.text == "the transcript body"


def test_download_of_other_users_job_is_404(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    other = repos.jobs.create_job(2, "file-x", "other.mp4", _now())
    repos.transcripts.create(other.id, 2, "secret", None, None, _now())
    repos.jobs.mark_completed(other.id, _now())
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)  # logs in as user id=1
        response = client.get(f"/jobs/{other.id}/download")

    assert response.status_code == 404


def test_download_of_pending_job_is_409(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    job = repos.jobs.create_job(1, "file-1", "meet.mp4", _now())  # pending
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)
        response = client.get(f"/jobs/{job.id}/download")

    assert response.status_code == 409


def test_jobs_page_lists_jobs_and_shows_download_and_drive_links(tmp_path):
    repos = build_memory_repositories()
    _seed_user1(repos)
    done = repos.jobs.create_job(1, "file-1", "meet.mp4", _now())
    repos.transcripts.create(done.id, 1, "body", None, None, _now())
    repos.jobs.mark_completed(done.id, _now(), transcript_drive_file_id="drive-xyz")
    pending = repos.jobs.create_job(1, "file-2", "pending.mp4", _now())
    app = create_app(_web_settings(tmp_path), repositories=repos)

    with TestClient(app) as client:
        _login(client)
        page = client.get("/jobs")

    text = page.text
    assert "meet.mp4" in text
    assert "pending.mp4" in text
    assert f"/jobs/{done.id}/download" in text        # Download button for completed job
    assert "drive.google.com/file/d/drive-xyz" in text  # Drive link when present
    # A non-completed job must NOT expose a download link (requirement 8).
    assert f"/jobs/{pending.id}/download" not in text


def test_jobs_page_handles_backend_unavailable_gracefully(tmp_path):
    # No repositories injected -> default postgres backend is unavailable on this branch.
    app = create_app(_web_settings(tmp_path), repositories=None)
    with TestClient(app) as client:
        _login(client)
        page = client.get("/jobs")
    assert page.status_code == 200
    assert "not available" in page.text.lower() or "postgres-core" in page.text
