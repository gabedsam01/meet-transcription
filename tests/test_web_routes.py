from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.web.config import WebSettings
from app.web.main import create_app


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
