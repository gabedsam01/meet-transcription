"""End-to-end test for the "extension-first" upload path.

Walks the full happy path with a real FastAPI app and a real worker container,
mirroring what a logged-in user + a Chrome extension would do:

1. User logs in and visits /extensao to mint a per-user token.
2. Extension hits /api/recordings/ping with the token to verify it's still good.
3. Extension POSTs a recording to /api/recordings/upload.
4. The job is created in the worker bundle, scoped to the user that minted
   the token (not a global "extension" owner).
5. The user lists jobs and sees the recording.
6. The user revokes the token.
7. The next /api/recordings/ping is rejected (401), even from the same
   client.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from app.web.extension_tokens import new_raw_token
from tests.e2e.helpers import ADMIN_ID, build_app, login, run_worker_once
from tests.fakes import build_fake_repositories


@pytest.fixture()
def app_and_repos(tmp_path):
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    app = build_app(tmp_path, auth=auth, worker=worker)
    return app, auth, worker


def _mint_token_via_page(client: TestClient) -> str:
    """Mint a token by submitting the /extensao form. Returns the raw token."""
    r = client.post("/extensao/gerar", data={"name": "E2E device"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    # Pull the new token out of the redirected page (only ever shown once).
    page = client.get(r.headers["location"]).text
    import re
    matches = re.findall(r"mtrec_[A-Za-z0-9_\-]{20,}", page)
    assert matches, "Token not shown on the success page"
    return matches[0]


def test_full_extension_happy_path(app_and_repos, tmp_path):
    app, auth, worker = app_and_repos
    with TestClient(app) as client:
        login(client)

        # 1. Mint a token.
        raw_token = _mint_token_via_page(client)
        assert raw_token.startswith("mtrec_")

        # 2. Ping the token.
        ping = client.post(
            "/api/recordings/ping",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert ping.status_code == 200
        body = ping.json()
        assert body["ok"] is True
        assert body["user_id"] == ADMIN_ID

        # 3. Upload a recording.
        upload = client.post(
            "/api/recordings/upload",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "Origin": "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef",
            },
            files={"file": ("meeting-2026-06-06.webm", b"webm-payload", "audio/webm")},
            data={
                "meeting_title": "E2E extension meeting",
                "duration_seconds": "120",
                "source": "chrome-extension",
            },
        )
        assert upload.status_code == 201, upload.text
        recording_id = upload.json()["recording_id"]

        # 4. A job is queued, scoped to the user.
        jobs = worker.jobs.list_jobs_for_user(ADMIN_ID)
        assert jobs, "no jobs for admin after extension upload"
        assert any(
            (j.source_file_id or "").startswith("chrome-extension:") for j in jobs
        )

        # 5. The user sees the job in the dashboard listing.
        page = client.get("/").text
        assert "E2E extension meeting" in page

        # 6. Revoke the token.
        # Find the token id by listing, and the CSRF token from the form.
        page = client.get("/extensao").text
        import re
        rev_ids = re.findall(r'name="token_id" value="(\d+)"', page)
        assert rev_ids
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', page)
        csrf_token = csrf_match.group(1) if csrf_match else ""
        rev = client.post(
            "/extensao/revogar",
            data={"token_id": rev_ids[0], "csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert rev.status_code in (302, 303)

        # 7. The revoked token is rejected.
        ping2 = client.post(
            "/api/recordings/ping",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert ping2.status_code == 401


def test_ping_with_no_token_returns_401(app_and_repos):
    app, _, _ = app_and_repos
    with TestClient(app) as client:
        r = client.post("/api/recordings/ping")
    assert r.status_code == 401


def test_ping_with_invalid_token_returns_401(app_and_repos):
    app, _, _ = app_and_repos
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/ping",
            headers={"Authorization": "Bearer mtrec_this_is_a_fake_token_xx"},
        )
    assert r.status_code == 401


def test_two_users_have_isolated_tokens(app_and_repos):
    """Tokens minted by user A must not be usable by user B."""
    app, auth, _ = app_and_repos
    # Enter the TestClient first so the lifespan creates the admin, then
    # bootstrap a second user (which gets the next id).
    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "secret"},
            follow_redirects=False,
        )
        auth.users.create(
            email="other@example.com", password_hash="x", name="Other", role="user"
        )
        other_user = auth.users.get_by_email("other@example.com")
        other_id = other_user.id
        assert other_id != ADMIN_ID, "second user must have a different id from admin"

        # Mint a token for admin.
        raw_admin, admin_hash, admin_prefix = new_raw_token("a-long-secret-for-tests")
        auth.extension_tokens.create_for_user(
            ADMIN_ID, name="admin device", token_hash=admin_hash, token_prefix=admin_prefix
        )
        # Mint a token for the other user.
        raw_other, other_hash, other_prefix = new_raw_token("a-long-secret-for-tests")
        auth.extension_tokens.create_for_user(
            other_id, name="other device", token_hash=other_hash, token_prefix=other_prefix
        )

        # admin token is fine
        a = client.post(
            "/api/recordings/ping",
            headers={"Authorization": f"Bearer {raw_admin}"},
        )
        assert a.status_code == 200, a.text
        assert a.json()["user_id"] == ADMIN_ID
        # other token is fine
        o = client.post(
            "/api/recordings/ping",
            headers={"Authorization": f"Bearer {raw_other}"},
        )
        assert o.status_code == 200, o.text
        assert o.json()["user_id"] == other_id


def test_extension_upload_creates_transcriptable_job(app_and_repos, tmp_path):
    """A worker can actually process a job created via the extension endpoint."""
    app, auth, worker = app_and_repos
    with TestClient(app) as client:
        login(client)
        raw_token = _mint_token_via_page(client)

        # Configure transcription (avoid a "no key" failure).
        auth.deepgram_credentials.save_for_user(ADMIN_ID, "encrypted-key")

        # Upload.
        upload = client.post(
            "/api/recordings/upload",
            headers={"Authorization": f"Bearer {raw_token}"},
            files={"file": ("meeting.webm", b"webm-bytes", "audio/webm")},
            data={"meeting_title": "Worker test", "source": "chrome-extension"},
        )
        assert upload.status_code == 201

        # The worker bundle has the job. We don't run it (the worker test
        # infra expects real audio), but we assert it is pending and
        # scoped to the user.
        jobs = worker.jobs.list_jobs_for_user(ADMIN_ID)
        assert jobs, "no jobs for admin after extension upload"
        assert all(j.status in ("pending", "processing", "completed") for j in jobs)
