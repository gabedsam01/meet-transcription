"""Tests for the Chrome-extension CORS middleware.

Covers:
- preflight (OPTIONS) on /api/recordings/* with a valid chrome-extension
  origin returns the right CORS headers and 204;
- preflight from an unrelated origin (e.g. https://evil.example) gets no
  CORS headers at all (no wildcard leak);
- a real POST from a chrome-extension origin gets the Access-Control-Allow-Origin
  echoed back, so the browser accepts the response;
- non-recording routes (e.g. /health, /jobs/*) do not get CORS headers even
  from a chrome-extension origin (CORS is scoped to /api/recordings/*);
- the middleware's regex only matches valid 32-letter Chrome extension ids.
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.cors import _CHROME_EXTENSION_ORIGIN
from app.web.main import create_app
from tests.fakes import build_fake_repositories


VALID_ORIGIN = "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef"  # 32 chars
EVIL_ORIGIN = "https://evil.example.com"
EVIL_CHROME = "chrome-extension://evil"  # too short, must be rejected


def _settings(tmp_path, *, token="legacy-token") -> WebSettings:
    return WebSettings.from_env({
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
        "EXTENSION_RECORDINGS_DIR": str(tmp_path / "recordings"),
        "EXTENSION_UPLOAD_TOKEN": token,
    })


def _app(tmp_path):
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    return create_app(_settings(tmp_path), repositories=auth, worker_repositories=worker)


# ---------------------------------------------------------------------------
# pure regex sanity
# ---------------------------------------------------------------------------


def test_chrome_extension_origin_regex_accepts_valid_id():
    assert _CHROME_EXTENSION_ORIGIN.match(VALID_ORIGIN)


def test_chrome_extension_origin_regex_rejects_short_id():
    assert not _CHROME_EXTENSION_ORIGIN.match(EVIL_CHROME)


def test_chrome_extension_origin_regex_rejects_non_chrome_scheme():
    assert not _CHROME_EXTENSION_ORIGIN.match(EVIL_ORIGIN)
    assert not _CHROME_EXTENSION_ORIGIN.match("http://abcdefghijklmnopqrstuvwxyz012345")
    assert not _CHROME_EXTENSION_ORIGIN.match("null")


def test_chrome_extension_origin_regex_rejects_uppercase():
    # Chrome ids are lowercase; uppercase is never a real id.
    assert not _CHROME_EXTENSION_ORIGIN.match(
        "chrome-extension://ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEF"
    )


def test_chrome_extension_origin_regex_rejects_trailing_garbage():
    # A crafted origin with a path/query after the id must not match.
    assert not _CHROME_EXTENSION_ORIGIN.match(VALID_ORIGIN + "/something")
    assert not _CHROME_EXTENSION_ORIGIN.match(VALID_ORIGIN + ".example.com")


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def test_preflight_with_chrome_extension_origin_returns_204(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.options(
            "/api/recordings/upload",
            headers={
                "Origin": VALID_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == VALID_ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "Authorization" in r.headers["access-control-allow-headers"]
    # Vary: Origin is required so shared caches don't leak between origins.
    assert "Origin" in r.headers["vary"]


def test_preflight_ping_endpoint_also_allowed(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.options(
            "/api/recordings/ping",
            headers={
                "Origin": VALID_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == VALID_ORIGIN


def test_preflight_with_evil_origin_returns_no_cors_headers(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.options(
            "/api/recordings/upload",
            headers={
                "Origin": EVIL_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in r.headers


def test_preflight_with_garbage_chrome_extension_origin_rejected(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.options(
            "/api/recordings/upload",
            headers={
                "Origin": EVIL_CHROME,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in r.headers


# ---------------------------------------------------------------------------
# real (non-preflight) request
# ---------------------------------------------------------------------------


def test_post_from_chrome_extension_origin_includes_cors_header(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        # Token is wrong, so the request itself 401s — but the CORS header
        # MUST be present so the browser exposes the body to the extension.
        r = client.post(
            "/api/recordings/upload",
            headers={"Origin": VALID_ORIGIN},
            files={"file": ("rec.webm", b"x", "audio/webm")},
        )
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == VALID_ORIGIN
    assert "Origin" in r.headers["vary"]


def test_post_from_evil_origin_has_no_cors_header(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/upload",
            headers={"Origin": EVIL_ORIGIN},
            files={"file": ("rec.webm", b"x", "audio/webm")},
        )
    # Either 401 (token missing) or no CORS header — both are acceptable; the
    # contract is just that the header is NOT present.
    assert "access-control-allow-origin" not in r.headers


def test_ping_post_from_chrome_extension_origin_includes_cors_header(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/ping",
            headers={"Origin": VALID_ORIGIN},
        )
    # 401 (no token) but with CORS header so the browser exposes the body.
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == VALID_ORIGIN


# ---------------------------------------------------------------------------
# scope: CORS is ONLY for /api/recordings/*
# ---------------------------------------------------------------------------


def test_cors_not_applied_to_health(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": VALID_ORIGIN})
    assert r.status_code == 200
    # CORS middleware is scoped to /api/recordings/*; /health must not echo.
    assert "access-control-allow-origin" not in r.headers


def test_cors_not_applied_to_jobs_download(tmp_path):
    # Even an authenticated download from a chrome-extension origin must NOT
    # gain CORS headers — that path is session-cookie only, not for the
    # extension.
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.get("/jobs/1/download", headers={"Origin": VALID_ORIGIN})
    # 401 (no session) is fine; the CORS header must not be present.
    assert "access-control-allow-origin" not in r.headers


def test_cors_not_applied_to_root(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        r = client.get("/", headers={"Origin": VALID_ORIGIN})
    # 303 to /login is fine; the CORS header must not be present.
    assert "access-control-allow-origin" not in r.headers
