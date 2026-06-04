import pytest

from app.web.config import WebSettings


def _env(tmp_path, **overrides):
    env = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql+psycopg://app:app@db:5432/meet",
        "TMP_DIR": str(tmp_path / "tmp"),
    }
    env.update(overrides)
    return env


def test_web_settings_parses_required_values(tmp_path):
    settings = WebSettings.from_env(_env(tmp_path))
    assert settings.admin_username == "admin"
    assert settings.session_cookie_secure is False
    assert settings.database_url.startswith("postgresql")
    assert settings.tmp_dir.name == "tmp"
    assert not hasattr(settings, "deepgram_api_key")


def test_web_settings_requires_app_secret_key(tmp_path):
    env = _env(tmp_path)
    del env["APP_SECRET_KEY"]
    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        WebSettings.from_env(env)


def test_web_settings_requires_database_url(tmp_path):
    env = _env(tmp_path)
    del env["DATABASE_URL"]
    with pytest.raises(ValueError, match="DATABASE_URL"):
        WebSettings.from_env(env)
