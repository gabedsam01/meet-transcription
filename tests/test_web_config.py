import pytest

from app.web.config import WebSettings


def _base_env(tmp_path) -> dict:
    return {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DEEPGRAM_API_KEY": "dg-key",
        "TMP_DIR": str(tmp_path / "tmp"),
    }


def test_web_settings_parses_required_values(tmp_path):
    settings = WebSettings.from_env(_base_env(tmp_path))

    assert settings.admin_username == "admin"
    assert settings.session_cookie_secure is False
    assert settings.tmp_dir.name == "tmp"
    # The database connection is no longer part of WebSettings; it comes from
    # DATABASE_URL via the app.database layer.
    assert not hasattr(settings, "database_path")


def test_web_settings_requires_app_secret_key(tmp_path):
    env = _base_env(tmp_path)
    del env["APP_SECRET_KEY"]

    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        WebSettings.from_env(env)
