import pytest

from app.web.config import WebSettings


def test_web_settings_parses_required_values(tmp_path):
    settings = WebSettings.from_env(
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

    assert settings.admin_username == "admin"
    assert settings.session_cookie_secure is False
    assert settings.database_path.name == "app.db"
    assert settings.tmp_dir.name == "tmp"


def test_web_settings_requires_app_secret_key(tmp_path):
    env = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": str(tmp_path / "app.db"),
        "DEEPGRAM_API_KEY": "dg-key",
    }

    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        WebSettings.from_env(env)
