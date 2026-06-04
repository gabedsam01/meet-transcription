from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.web.config import WebSettings
from app.web.deepgram_key import DeepgramKeyStore
from app.web.main import create_app
from app.web.repositories import DriveSettings, GoogleToken
from app.web.security import fernet_from_secret
from tests.fakes import build_fake_repositories


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
    client, repos = _client(tmp_path)
    folder = "1A2b3C4d5E6f7G8h9I0jKl"
    with client:
        _login(client)
        admin = repos.users.get_by_email("admin")
        repos.drive_settings.save_for_user(admin.id, DriveSettings("url", folder, None, None, False))
        repos.google_tokens.save_for_user(admin.id, GoogleToken("a", "r", "u", "c", "s", "sc", None))
        client.post("/jobs/run-once", follow_redirects=False)
        assert repos.jobs.list_jobs_for_user(admin.id) == []
        page = client.get("/jobs").text
        assert "Configure sua Deepgram API Key antes de iniciar uma transcrição." in page


def test_run_once_enqueues_pending_when_ready(tmp_path):
    client, repos = _client(tmp_path)
    folder = "1A2b3C4d5E6f7G8h9I0jKl"
    with client:
        _login(client)
        admin = repos.users.get_by_email("admin")
        repos.drive_settings.save_for_user(admin.id, DriveSettings("url", folder, None, None, False))
        repos.google_tokens.save_for_user(admin.id, GoogleToken("a", "r", "u", "c", "s", "sc", None))
        DeepgramKeyStore(repos.deepgram_credentials,
                         fernet_from_secret("a-long-secret-for-tests")).save_for_user(admin.id, "dg-key")
        client.post("/jobs/run-once", follow_redirects=False)
        jobs = repos.jobs.list_jobs_for_user(admin.id)
        assert len(jobs) == 1 and jobs[0].status == "pending"


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
