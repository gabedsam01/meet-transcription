from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.database.repositories import (
    GoogleTokenRepository,
    TranscriptionJobRepository,
    UserDriveSettingsRepository,
    UserRepository,
)
from app.database.session import session_scope
from app.web.config import WebSettings
from app.web.main import create_app
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore

_SECRET = "a-long-secret-for-tests"


def test_health_returns_ok(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_protected_dashboard_redirects_to_login(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/", follow_redirects=False)

        assert response.status_code in {302, 303, 307}
        assert response.headers["location"].startswith("/login")


def test_login_sets_http_only_session_cookie(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post(
            "/login",
            data={"username": "admin", "password": "secret"},
            follow_redirects=False,
        )

        assert response.status_code in {302, 303}
        assert response.headers["location"] == "/"
        assert "httponly" in response.headers["set-cookie"].lower()


def test_login_promotes_existing_user_to_admin(pg, tmp_path):
    with session_scope() as s:
        UserRepository(s).create(email="admin", name="admin", role="user")

    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)

    with session_scope() as s:
        user = UserRepository(s).get_by_email("admin")
        assert user.role == "admin"
        assert user.is_active is True


def test_authenticated_settings_and_jobs_render(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)

        assert client.get("/settings").status_code == 200
        assert client.get("/jobs").status_code == 200


def test_settings_save_persists_folders(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)
        response = client.post(
            "/settings",
            data={"source_drive_folder_id": "src-1", "destination_drive_folder_id": "dst-1"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    with session_scope() as s:
        user = UserRepository(s).get_by_email("admin")
        saved = UserDriveSettingsRepository(s).get_for_user(user.id)
        assert saved.source_drive_folder_id == "src-1"
        assert saved.destination_drive_folder_id == "dst-1"


def test_connect_google_redirects_and_stores_state(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)

        response = client.get("/connect-google", follow_redirects=False)

        assert response.status_code in {302, 303, 307}
        assert "accounts.google.com" in response.headers["location"]
        assert "state=" in response.headers["location"]


def test_oauth_callback_accepts_state_saved_by_connect_google(pg, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    exchanged_codes = []

    def fake_exchange_google_code(web_settings, code):
        exchanged_codes.append((web_settings, code))
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": web_settings.google_web_client_id,
            "client_secret": web_settings.google_web_client_secret,
            "scopes": "https://www.googleapis.com/auth/drive",
            "expiry": "2026-06-03T00:00:00+00:00",
        }

    monkeypatch.setattr("app.web.main.exchange_google_code", fake_exchange_google_code)
    with TestClient(create_app(settings)) as client:
        _login(client)
        connect_response = client.get("/connect-google", follow_redirects=False)
        state = parse_qs(urlparse(connect_response.headers["location"]).query)["state"][0]

        callback_response = client.get(
            f"/oauth/google/callback?code=abc&state={state}",
            follow_redirects=False,
        )

        assert callback_response.status_code in {302, 303}
        assert callback_response.headers["location"] == "/"
        assert exchanged_codes == [(settings, "abc")]

    with session_scope() as s:
        user = UserRepository(s).get_by_email("admin")
        assert GoogleTokenRepository(s).get_for_user(user.id) is not None


def test_oauth_callback_rejects_mismatched_state(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)
        client.get("/connect-google", follow_redirects=False)

        response = client.get("/oauth/google/callback?code=abc&state=wrong")

        assert response.status_code == 400


def test_run_once_responds_fast_with_pending_job_and_background_task(pg, tmp_path, monkeypatch):
    user_id = _seed_admin_with_settings_and_token()
    scheduled = []
    monkeypatch.setattr(
        "app.web.services.run_user_job_background",
        lambda s, job_id, user: scheduled.append((job_id, user)),
    )

    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)

        response = client.post("/jobs/run-once", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"

        jobs = _jobs_for(user_id)
        assert len(jobs) == 1
        # Request path must NOT process synchronously: the stubbed background
        # task never ran the work, so the job is still pending.
        assert jobs[0][1] == "pending"
        assert scheduled == [(jobs[0][0], user_id)]

        page = client.get("/jobs")
        assert "Job started" in page.text


def test_run_once_blocks_when_a_job_is_already_running(pg, tmp_path, monkeypatch):
    user_id = _seed_admin_with_settings_and_token()
    with session_scope() as s:
        TranscriptionJobRepository(s).create(user_id=user_id, status="processing")
    scheduled = []
    monkeypatch.setattr(
        "app.web.services.run_user_job_background",
        lambda s, job_id, user: scheduled.append((job_id, user)),
    )

    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)

        response = client.post("/jobs/run-once", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"
        assert scheduled == []
        assert len(_jobs_for(user_id)) == 1

        page = client.get("/jobs")
        assert "There is already a job running." in page.text


def test_run_once_without_settings_redirects_with_message(pg, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)  # admin user exists, but no settings or Google token

        response = client.post("/jobs/run-once", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"

        page = client.get("/jobs")
        assert "Configure source and destination folders" in page.text

    with session_scope() as s:
        user = UserRepository(s).get_by_email("admin")
        assert list(TranscriptionJobRepository(s).list_for_user(user.id)) == []


def test_jobs_page_shows_all_job_fields_and_refresh_guidance(pg, tmp_path):
    user_id = _seed_admin_with_settings_and_token()
    with session_scope() as s:
        repo = TranscriptionJobRepository(s)
        job = repo.create(
            user_id=user_id,
            status="pending",
            source_file_id="src-123",
            source_file_name="meeting.mp4",
        )
        repo.update(
            job.id,
            status="completed",
            transcript_drive_file_id="txt-456",
            attempts=1,
        )

    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)
        page = client.get("/jobs")

    text = page.text
    assert "meeting.mp4" in text
    assert "src-123" in text
    assert "txt-456" in text
    assert "completed" in text
    assert "After starting a job, refresh this page to see updates." in text
    for header in ["Source ID", "Transcript", "Attempts", "Created", "Updated", "Processed"]:
        assert header in text


def test_jobs_page_shows_error_message_for_failed_job(pg, tmp_path):
    user_id = _seed_admin_with_settings_and_token()
    with session_scope() as s:
        repo = TranscriptionJobRepository(s)
        job = repo.create(user_id=user_id, status="pending")
        repo.update(
            job.id,
            status="failed",
            error_message="Deepgram exploded mid-transcription",
        )

    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)
        page = client.get("/jobs")

    assert "failed" in page.text
    assert "Deepgram exploded mid-transcription" in page.text


# --- helpers ----------------------------------------------------------------


def _seed_admin_with_settings_and_token() -> int:
    with session_scope() as s:
        user = UserRepository(s).get_or_create(email="admin", name="admin", role="admin")
        UserDriveSettingsRepository(s).upsert_for_user(
            user.id, source_drive_folder_id="source", destination_drive_folder_id="dest"
        )
        TokenStore(fernet_from_secret(_SECRET)).save_for_user(
            s,
            user.id,
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scopes": "https://www.googleapis.com/auth/drive",
                "expiry": "2026-06-03T00:00:00+00:00",
            },
        )
        return user.id


def _jobs_for(user_id: int):
    with session_scope() as s:
        return [(j.id, j.status) for j in TranscriptionJobRepository(s).list_for_user(user_id)]


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env(
        {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "secret",
            "APP_SECRET_KEY": _SECRET,
            "SESSION_COOKIE_SECURE": "false",
            "GOOGLE_WEB_CLIENT_ID": "client-id",
            "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
            "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
            "DEEPGRAM_API_KEY": "dg-key",
            "TMP_DIR": str(tmp_path / "tmp"),
        }
    )
