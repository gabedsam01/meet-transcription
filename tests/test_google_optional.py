"""Tests for the "Google Drive is optional" path.

Covers:
- WebSettings.from_env() boots cleanly when all Google envs are missing
  (google_enabled=False);
- the dashboard, onboarding and /settings/drive pages render without
  crashing when Google is absent;
- the /connect-google route is a friendly redirect when Google envs are
  missing, never crashing on the OAuth URL builder;
- the extension upload + ping endpoints work without any Google envs
  (extension-first path is the primary one when Google is absent).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.extension_tokens import new_raw_token
from app.web.main import create_app
from tests.fakes import build_fake_repositories


def _settings(tmp_path, *, with_google=True, token="legacy-token") -> WebSettings:
    env = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
        "EXTENSION_RECORDINGS_DIR": str(tmp_path / "recordings"),
    }
    if with_google:
        env["GOOGLE_WEB_CLIENT_ID"] = "client-id"
        env["GOOGLE_WEB_CLIENT_SECRET"] = "client-secret"
        env["GOOGLE_REDIRECT_URI"] = "http://localhost:8000/oauth/google/callback"
    if token:
        env["EXTENSION_UPLOAD_TOKEN"] = token
    return WebSettings.from_env(env)


def _app(tmp_path, *, with_google=True, token="legacy-token"):
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    return create_app(
        _settings(tmp_path, with_google=with_google, token=token),
        repositories=auth,
        worker_repositories=worker,
    )


def _login(client):
    r = client.post(
        "/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    return r


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_settings_boot_without_google_envs(tmp_path):
    settings = _settings(tmp_path, with_google=False)
    assert settings.google_enabled is False
    assert settings.google_web_client_id == ""
    assert settings.google_web_client_secret == ""
    assert settings.google_redirect_uri == ""


def test_settings_boot_with_google_envs(tmp_path):
    settings = _settings(tmp_path, with_google=True)
    assert settings.google_enabled is True


def test_settings_partial_google_envs_disables_drive(tmp_path):
    # Only one of the three envs is set — Google is not usable. The flag must
    # be False so the UI never tries to build a partial OAuth URL.
    env = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
        "GOOGLE_WEB_CLIENT_ID": "x",
        # Missing CLIENT_SECRET and REDIRECT_URI.
    }
    settings = WebSettings.from_env(env)
    assert settings.google_enabled is False


# ---------------------------------------------------------------------------
# /connect-google without Google envs
# ---------------------------------------------------------------------------


def test_connect_google_friendly_redirect_when_disabled(tmp_path):
    app = _app(tmp_path, with_google=False)
    with TestClient(app) as client:
        _login(client)
        r = client.get("/connect-google", follow_redirects=False)
    # 303 to a friendly URL (the drive settings page) — never 500.
    assert r.status_code == 303
    assert r.headers["location"].startswith("/settings/drive")


def test_connect_google_works_with_google_envs(tmp_path):
    app = _app(tmp_path, with_google=True)
    with TestClient(app) as client:
        _login(client)
        r = client.get("/connect-google", follow_redirects=False)
    # 303 to the Google OAuth URL.
    assert r.status_code == 303
    assert "accounts.google.com" in r.headers["location"]


# ---------------------------------------------------------------------------
# pages render without Google envs
# ---------------------------------------------------------------------------


def test_dashboard_renders_without_google(tmp_path):
    app = _app(tmp_path, with_google=False)
    with TestClient(app) as client:
        _login(client)
        page = client.get("/")
    assert page.status_code == 200
    assert "Meet Transcription" in page.text or "Painel" in page.text


def test_drive_settings_page_renders_without_google(tmp_path):
    app = _app(tmp_path, with_google=False)
    with TestClient(app) as client:
        _login(client)
        page = client.get("/settings/drive")
    assert page.status_code == 200
    assert "Google Drive desativado" in page.text


def test_onboarding_renders_without_google(tmp_path):
    app = _app(tmp_path, with_google=False)
    with TestClient(app) as client:
        _login(client)
        page = client.get("/onboarding")
    assert page.status_code == 200
    # The onboarding reflects the "Google desativado" state.
    assert "Google Drive desativado" in page.text


# ---------------------------------------------------------------------------
# extension-first flow works without Google envs
# ---------------------------------------------------------------------------


def test_extension_upload_works_without_google_envs(tmp_path):
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    app = create_app(
        _settings(tmp_path, with_google=False, token=""),
        repositories=auth,
        worker_repositories=worker,
    )
    # Generate a per-user token and upload through the (Google-less) app.
    raw, token_hash, prefix = new_raw_token("a-long-secret-for-tests")
    auth.extension_tokens.create_for_user(
        1, name="Device", token_hash=token_hash, token_prefix=prefix
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/upload",
            headers={"Authorization": f"Bearer {raw}"},
            files={"file": ("rec.webm", b"webm-bytes", "audio/webm")},
            data={
                "meeting_title": "Sem Google",
                "duration_seconds": "60",
                "source": "chrome-extension",
            },
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["recording_id"]


def test_extension_ping_works_without_google_envs(tmp_path):
    app = _app(tmp_path, with_google=False, token="")
    with TestClient(app) as client:
        r = client.post("/api/recordings/ping")
    # 401 (no token) — but the request must reach the handler, not 500.
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /ready still works without Google envs
# ---------------------------------------------------------------------------


def test_ready_endpoint_works_without_google_envs(tmp_path):
    app = _app(tmp_path, with_google=False)
    with TestClient(app) as client:
        r = client.get("/ready")
    # 200 (Postgres-backed in tests) or 503 (no DB) — never 500.
    assert r.status_code in (200, 503)
