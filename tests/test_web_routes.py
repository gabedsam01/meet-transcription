from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import db
from app.web.config import WebSettings
from app.web.main import create_app
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore


def test_health_returns_ok(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_create_app_initializes_database_on_startup(tmp_path):
    settings = _settings(tmp_path)

    app = create_app(settings)

    assert not settings.database_path.exists()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert settings.database_path.exists()


def test_protected_dashboard_redirects_to_login(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/", follow_redirects=False)

        assert response.status_code in {302, 303, 307}
        assert response.headers["location"].startswith("/login")


def test_login_sets_http_only_session_cookie(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post(
            "/login",
            data={"username": "admin", "password": "secret"},
            follow_redirects=False,
        )

        assert response.status_code in {302, 303}
        assert response.headers["location"] == "/"
        assert "httponly" in response.headers["set-cookie"].lower()


def test_authenticated_settings_and_jobs_render(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)

        assert client.get("/settings").status_code == 200
        assert client.get("/jobs").status_code == 200


def test_connect_google_redirects_and_stores_state(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)

        response = client.get("/connect-google", follow_redirects=False)

        assert response.status_code in {302, 303, 307}
        assert "accounts.google.com" in response.headers["location"]
        assert "state=" in response.headers["location"]


def test_oauth_callback_accepts_state_saved_by_connect_google(tmp_path, monkeypatch):
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


def test_oauth_callback_rejects_mismatched_state(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        _login(client)
        client.get("/connect-google", follow_redirects=False)

        response = client.get("/oauth/google/callback?code=abc&state=wrong")

        assert response.status_code == 400


def test_run_once_responds_fast_with_pending_job_and_background_task(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user = _seed_admin_with_settings_and_token(settings)
    scheduled = []
    monkeypatch.setattr(
        "app.web.services.run_user_job_background",
        lambda s, job_id, user_id: scheduled.append((job_id, user_id)),
    )

    with TestClient(create_app(settings)) as client:
        _login(client)

        response = client.post("/jobs/run-once", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"

        jobs = db.list_jobs(settings.database_path, user["id"])
        assert len(jobs) == 1
        # Request path must NOT process synchronously: the stubbed background
        # task never ran the work, so the job is still pending.
        assert jobs[0]["status"] == "pending"
        assert scheduled == [(jobs[0]["id"], user["id"])]

        page = client.get("/jobs")
        assert "Job started" in page.text


def test_run_once_blocks_when_a_job_is_already_running(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user = _seed_admin_with_settings_and_token(settings)
    db.create_job(settings.database_path, user["id"], status="processing")
    scheduled = []
    monkeypatch.setattr(
        "app.web.services.run_user_job_background",
        lambda s, job_id, user_id: scheduled.append((job_id, user_id)),
    )

    with TestClient(create_app(settings)) as client:
        _login(client)

        response = client.post("/jobs/run-once", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"
        assert scheduled == []
        assert len(db.list_jobs(settings.database_path, user["id"])) == 1

        page = client.get("/jobs")
        assert "There is already a job running." in page.text


def test_run_once_without_settings_redirects_with_message(tmp_path):
    settings = _settings(tmp_path)

    with TestClient(create_app(settings)) as client:
        _login(client)  # admin user exists, but no settings or Google token

        response = client.post("/jobs/run-once", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"

        user = db.get_or_create_user(settings.database_path, "admin")
        assert db.list_jobs(settings.database_path, user["id"]) == []

        page = client.get("/jobs")
        assert "Configure source and destination folders" in page.text


def test_jobs_page_lists_jobs_with_status_badge_and_download_link(tmp_path):
    settings = _settings(tmp_path)
    user = _seed_admin_with_settings_and_token(settings)
    job = db.create_job(
        settings.database_path,
        user["id"],
        status="pending",
        source_file_id="src-123",
        source_file_name="meeting.mp4",
    )
    db.update_job(
        settings.database_path,
        job["id"],
        status="completed",
        transcript_drive_file_id="txt-456",
        attempts=1,
    )

    with TestClient(create_app(settings)) as client:
        _login(client)
        page = client.get("/jobs")

    text = page.text
    assert "meeting.mp4" in text
    assert "src-123" in text  # short id is shown in full
    assert "completed" in text  # status badge label
    assert "Download TXT" in text  # transcript shown as a link, not a raw id
    assert "txt-456" in text  # raw id appears only inside the download href
    assert f'/jobs/{job["id"]}' in text  # file name links to the detail page
    assert "After starting a job, refresh this page to see updates." in text
    for header in ["File", "Source", "Status", "Transcript", "Created"]:
        assert header in text


def test_failed_job_shows_badge_on_list_and_error_on_detail(tmp_path):
    settings = _settings(tmp_path)
    user = _seed_admin_with_settings_and_token(settings)
    job = db.create_job(settings.database_path, user["id"], status="pending")
    db.update_job(
        settings.database_path,
        job["id"],
        status="failed",
        error_message="Deepgram exploded mid-transcription",
    )

    with TestClient(create_app(settings)) as client:
        _login(client)
        list_page = client.get("/jobs")
        detail_page = client.get(f"/jobs/{job['id']}")

    # Status is a badge on the list; the long error text is kept off the list so
    # it never blows out the table, and lives on the detail page instead.
    assert "badge-failed" in list_page.text
    assert "Deepgram exploded mid-transcription" not in list_page.text
    assert "Deepgram exploded mid-transcription" in detail_page.text


def _seed_admin_with_settings_and_token(settings: WebSettings):
    db.init_db(settings.database_path)
    user = db.get_or_create_user(settings.database_path, "admin", "admin")
    db.save_settings(settings.database_path, user["id"], "source", "dest", 60)
    TokenStore(
        settings.database_path, fernet_from_secret(settings.app_secret_key)
    ).save_for_user(
        user["id"],
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
    return user


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
