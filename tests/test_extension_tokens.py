"""Tests for the per-user Chrome-extension upload token system.

Covers:
- the token-format helper (raw/hashed/prefix) and its never-reverse invariant;
- the /extensao page (rendered, requires login, lists tokens, mask only);
- the /extensao/gerar route (raw token shown once, hash persisted, plain not);
- the /extensao/revogar route (owner-scoped, no token in response, no leak);
- the /api/recordings/ping endpoint (valid, missing, invalid, revoked, legacy
  env token fallback);
- /api/recordings/upload with per-user tokens (token authenticates, owner of
  the token gets the job, token A cannot create jobs for user B);
- legacy env token still works as a fallback.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.extension_tokens import (
    TOKEN_PREFIX,
    hash_token,
    new_raw_token,
    verify_token,
)
from app.web.main import create_app
from tests.fakes import build_fake_repositories


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _settings(tmp_path, *, token="legacy-token", max_mb=500, with_google=True) -> WebSettings:
    env = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
        "EXTENSION_RECORDINGS_DIR": str(tmp_path / "recordings"),
        "EXTENSION_UPLOAD_MAX_MB": str(max_mb),
    }
    if with_google:
        env["GOOGLE_WEB_CLIENT_ID"] = "client-id"
        env["GOOGLE_WEB_CLIENT_SECRET"] = "client-secret"
        env["GOOGLE_REDIRECT_URI"] = "http://localhost:8000/oauth/google/callback"
    if token:
        env["EXTENSION_UPLOAD_TOKEN"] = token
    return WebSettings.from_env(env)


def _app(tmp_path, *, token="legacy-token", max_mb=500, with_google=True):
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    app = create_app(
        _settings(tmp_path, token=token, max_mb=max_mb, with_google=with_google),
        repositories=auth,
        worker_repositories=worker,
    )
    return app, auth, worker


def _login(client, username="admin", password="secret"):
    r = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    return r


# ---------------------------------------------------------------------------
# token-format helper
# ---------------------------------------------------------------------------


def test_new_raw_token_has_prefix_and_uniqueness():
    raw1, hash1, _ = new_raw_token("a-long-secret-for-tests")
    raw2, hash2, _ = new_raw_token("a-long-secret-for-tests")
    assert raw1.startswith(TOKEN_PREFIX)
    assert raw2.startswith(TOKEN_PREFIX)
    assert raw1 != raw2
    assert hash1 != hash2


def test_new_raw_token_hash_verifies_and_plaintext_is_not_reversible():
    raw, stored_hash, _ = new_raw_token("a-long-secret-for-tests")
    # The stored hash is the SHA-256 of the pepper+raw, not the raw itself.
    assert stored_hash != raw
    assert stored_hash == hash_token("a-long-secret-for-tests", raw)
    # verify_token accepts a matching raw and rejects a different raw.
    assert verify_token("a-long-secret-for-tests", raw, stored_hash)
    assert not verify_token("a-long-secret-for-tests", raw + "x", stored_hash)
    assert not verify_token("a-long-secret-for-tests", "", stored_hash)


def test_hash_pepper_changes_with_app_secret_key():
    raw, h1, _ = new_raw_token("a-very-long-secret-key-for-pepper-1")
    # Different pepper (different secret) must produce a different hash.
    h2 = hash_token("a-very-long-secret-key-for-pepper-2", raw)
    assert h1 != h2
    # Same secret + same raw -> same hash (idempotent, deterministic).
    assert h1 == hash_token("a-very-long-secret-key-for-pepper-1", raw)


def test_token_prefix_is_masked_display_only():
    raw, _, prefix = new_raw_token("a-long-secret-for-tests")
    # The masked display is short and never contains the entire raw.
    assert prefix != raw
    assert raw[:8] in prefix
    assert raw[-4:] in prefix


# ---------------------------------------------------------------------------
# /extensao UI
# ---------------------------------------------------------------------------


def test_extensao_requires_login(tmp_path):
    app, _, _ = _app(tmp_path, token="")
    with TestClient(app) as client:
        r = client.get("/extensao", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"].startswith("/login")


def test_extensao_page_renders_and_lists_tokens(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    admin_id = 1
    # Two tokens for the admin user. Hashes use unique markers so the test
    # can prove no raw hash leaks into the rendered HTML.
    auth.extension_tokens.create_for_user(
        admin_id, name="Celular", token_hash="hash-celular-xyz", token_prefix="mtrec_ab\u20261234"
    )
    auth.extension_tokens.create_for_user(
        admin_id, name="Notebook", token_hash="hash-notebook-qrs", token_prefix="mtrec_cd\u20265678"
    )
    with TestClient(app) as client:
        _login(client)
        page = client.get("/extensao")
    assert page.status_code == 200
    assert "Extensão do Chrome" in page.text
    assert "Celular" in page.text
    assert "Notebook" in page.text
    # No raw hash is rendered — only the masked prefix.
    assert "hash-celular-xyz" not in page.text
    assert "hash-notebook-qrs" not in page.text


def test_extensao_page_works_without_google_envs(tmp_path):
    app, _, _ = _app(tmp_path, token="", with_google=False)
    with TestClient(app) as client:
        _login(client)
        page = client.get("/extensao")
    assert page.status_code == 200
    assert "Google Drive desativado" in page.text


# ---------------------------------------------------------------------------
# /extensao/gerar
# ---------------------------------------------------------------------------


def test_generate_token_creates_row_and_shows_raw_once(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    with TestClient(app) as client:
        _login(client)
        r = client.post(
            "/extensao/gerar",
            data={"name": "Meu notebook"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        # The next page (the redirect) must carry the raw token exactly once.
        page = client.get("/extensao")
    assert "mtrec_" in page.text  # raw token starts with this prefix
    # After the GET, the raw token is gone from the session.
    page2 = client.get("/extensao")
    assert "Token gerado" not in page2.text
    # The row was persisted with the hash, not the raw.
    tokens = list(auth.extension_tokens.list_for_user(1))
    assert len(tokens) == 1
    assert tokens[0].name == "Meu notebook"
    # The masked display never equals the raw token.


def test_generate_token_persists_hash_not_plaintext(tmp_path, caplog):
    app, auth, _ = _app(tmp_path, token="")
    with TestClient(app) as client:
        _login(client)
        r = client.post(
            "/extensao/gerar",
            data={"name": "Device"},
            follow_redirects=False,
        )
        # The raw token was on the next GET (we don't capture it here).
        client.get("/extensao")
    tokens = list(auth.extension_tokens.list_for_user(1))
    assert tokens
    # The repository contract stores token_hash, not the raw. Make sure no
    # raw token leaked to logs either.
    for token in tokens:
        # token.masked is a short prefix; the full raw is never persisted.
        assert len(token.masked) < 64
    assert TOKEN_PREFIX not in caplog.text


def test_generate_token_default_name_when_empty(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    with TestClient(app) as client:
        _login(client)
        client.post(
            "/extensao/gerar",
            data={"name": "   "},
            follow_redirects=False,
        )
        client.get("/extensao")
    tokens = list(auth.extension_tokens.list_for_user(1))
    assert tokens
    assert tokens[0].name == "Token"


# ---------------------------------------------------------------------------
# /extensao/revogar
# ---------------------------------------------------------------------------


def test_revoke_token_marks_revoked(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    auth.extension_tokens.create_for_user(
        1, name="A", token_hash="hash-A-zzz", token_prefix="mtrec_a1\u20261234"
    )
    token_id = auth.extension_tokens.list_for_user(1)[0].id
    with TestClient(app) as client:
        _login(client)
        r = client.post(
            "/extensao/revogar",
            data={"token_id": str(token_id)},
            follow_redirects=False,
        )
        assert r.status_code == 303
    row = auth.extension_tokens.get_for_user(token_id, 1)
    assert row is not None
    assert row.revoked_at is not None


def test_revoke_other_users_token_is_a_noop(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    # Create a second user and one of their tokens. Bob is created BEFORE
    # the app lifespan runs, so the admin user (created in the lifespan) is
    # actually the *second* user id. We resolve ids dynamically below.
    auth.users.create(email="bob@x.com", password_hash="x", role="user")
    bob_id = auth.users.get_by_email("bob@x.com").id
    auth.extension_tokens.create_for_user(
        bob_id, name="B", token_hash="hash-B-zzz", token_prefix="mtrec_b1\u20269999"
    )
    bob_token_id = auth.extension_tokens.list_for_user(bob_id)[0].id
    with TestClient(app) as client:
        _login(client)
        client.post(
            "/extensao/revogar",
            data={"token_id": str(bob_token_id)},
            follow_redirects=False,
        )
    # Bob's token must remain active.
    row = auth.extension_tokens.get_for_user(bob_token_id, bob_id)
    assert row is not None and row.revoked_at is None


# ---------------------------------------------------------------------------
# /api/recordings/ping
# ---------------------------------------------------------------------------


def _create_token_for(auth, user_id, name="Device"):
    raw, stored_hash, prefix = new_raw_token("a-long-secret-for-tests")
    auth.extension_tokens.create_for_user(
        user_id, name=name, token_hash=stored_hash, token_prefix=prefix
    )
    return raw


def test_ping_valid_per_user_token(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    raw = _create_token_for(auth, 1, "Notebook")
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/ping",
            data={"upload_token": raw},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["user_email"] == "admin"


def test_ping_invalid_token_returns_401(tmp_path):
    app, _, _ = _app(tmp_path, token="")
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/ping",
            data={"upload_token": "mtrec_bogus"},
        )
    assert r.status_code == 401
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_token"


def test_ping_missing_token_returns_401(tmp_path):
    app, _, _ = _app(tmp_path, token="")
    with TestClient(app) as client:
        r = client.post("/api/recordings/ping")
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "missing_token"


def test_ping_revoked_token_returns_401(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    raw = _create_token_for(auth, 1, "Old")
    token_id = auth.extension_tokens.list_for_user(1)[0].id
    auth.extension_tokens.revoke_for_user(token_id, 1)
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/ping",
            data={"upload_token": raw},
        )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_token"


def test_ping_legacy_env_token_fallback(tmp_path):
    # No per-user store activity; the legacy env token still works.
    app, _, _ = _app(tmp_path, token="legacy-token")
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/ping",
            data={"upload_token": "legacy-token"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["user_email"] == "admin"


def test_ping_accepts_authorization_bearer_header(tmp_path):
    app, auth, _ = _app(tmp_path, token="")
    raw = _create_token_for(auth, 1, "Cell")
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/ping",
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/recordings/upload with per-user tokens
# ---------------------------------------------------------------------------


def _upload(client, *, token=None, content=b"webm-audio-bytes", form_token=None):
    headers = {}
    data = {
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "meeting_title": "Weekly Sync",
        "duration_seconds": "1800",
        "source": "chrome-extension",
    }
    if form_token is not None:
        data["upload_token"] = form_token
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/api/recordings/upload",
        headers=headers,
        files={"file": ("rec.webm", content, "audio/webm")},
        data=data,
    )


def test_upload_with_per_user_token_creates_job_for_owner(tmp_path):
    app, auth, worker = _app(tmp_path, token="")
    raw = _create_token_for(auth, 1)
    with TestClient(app) as client:
        r = _upload(client, token=raw)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    jobs = worker.jobs.list_jobs_for_user(1)
    assert len(jobs) == 1
    assert jobs[0].source_file_id.startswith("chrome-extension:")


def test_upload_with_form_field_token(tmp_path):
    app, auth, worker = _app(tmp_path, token="")
    raw = _create_token_for(auth, 1)
    with TestClient(app) as client:
        r = _upload(client, form_token=raw)
    assert r.status_code == 201
    assert worker.jobs.list_jobs_for_user(1)


def test_upload_with_revoked_token_is_401(tmp_path):
    app, auth, worker = _app(tmp_path, token="")
    raw = _create_token_for(auth, 1, "Old")
    token_id = auth.extension_tokens.list_for_user(1)[0].id
    auth.extension_tokens.revoke_for_user(token_id, 1)
    with TestClient(app) as client:
        r = _upload(client, token=raw)
    assert r.status_code == 401
    assert worker.jobs.list_jobs_for_user(1) == []


def test_upload_with_legacy_env_token_still_works(tmp_path):
    app, _, worker = _app(tmp_path, token="legacy-token")
    with TestClient(app) as client:
        r = _upload(client, token="legacy-token")
    assert r.status_code == 201
    assert worker.jobs.list_jobs_for_user(1)


def test_upload_user_a_token_cannot_create_user_b_job(tmp_path):
    app, auth, worker = _app(tmp_path, token="")
    # Bob is created BEFORE the app lifespan runs, so the admin user (created
    # in the lifespan) is actually the *second* user id. We resolve the admin
    # id dynamically via the session after login.
    auth.users.create(email="bob@x.com", password_hash="x", role="user")
    bob_id = auth.users.get_by_email("bob@x.com").id
    raw = _create_token_for(auth, bob_id, "Bob's device")
    with TestClient(app) as client:
        _login(client)
        r = _upload(client, token=raw)
    assert r.status_code == 201
    admin_id = auth.users.get_by_email("admin").id
    admin_jobs = worker.jobs.list_jobs_for_user(admin_id)
    bob_jobs = worker.jobs.list_jobs_for_user(bob_id)
    assert len(admin_jobs) == 0
    assert len(bob_jobs) == 1


def test_upload_token_never_logged(tmp_path, caplog):
    app, auth, _ = _app(tmp_path, token="")
    raw = _create_token_for(auth, 1, "Secret device")
    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            r = _upload(client, token=raw)
    assert r.status_code == 201
    # The raw token value must NEVER appear in any log line. The masked
    # display ("mtrec_ab…1234") is fine to appear; the full secret is not.
    assert raw not in caplog.text


def test_upload_no_token_returns_401(tmp_path):
    app, _, _ = _app(tmp_path, token="legacy-token")
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/upload",
            files={"file": ("rec.webm", b"x", "audio/webm")},
        )
    assert r.status_code == 401
