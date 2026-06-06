import logging
from dataclasses import replace

from fastapi.testclient import TestClient

from app.recordings import recording_id_from_source, resolve_recording_file
from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.main import create_app
from tests.fakes import build_fake_repositories

TOKEN = "super-secret-upload-token"


def _settings(tmp_path, *, token=TOKEN, max_mb=500) -> WebSettings:
    env = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
        "EXTENSION_RECORDINGS_DIR": str(tmp_path / "recordings"),
        "EXTENSION_UPLOAD_MAX_MB": str(max_mb),
    }
    if token:
        env["EXTENSION_UPLOAD_TOKEN"] = token
    return WebSettings.from_env(env)


def _app(tmp_path, *, token=TOKEN, max_mb=500, with_extension_store=True):
    worker = build_memory_repositories()
    auth = build_fake_repositories()
    if not with_extension_store:
        # Simulate a deploy without the extension-tokens table (e.g. an
        # older schema that hasn't run the new migration): the per-user path
        # is gone, so the feature is OFF unless the legacy env token is set.
        auth = replace(auth, extension_tokens=None)
    app = create_app(
        _settings(tmp_path, token=token, max_mb=max_mb),
        repositories=auth,
        worker_repositories=worker,
    )
    return app, worker


def _upload(client, *, token=TOKEN, content=b"webm-audio-bytes", filename="rec.webm"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/api/recordings/upload",
        headers=headers,
        files={"file": (filename, content, "audio/webm")},
        data={
            "meeting_url": "https://meet.google.com/abc-defg-hij",
            "meeting_title": "Weekly Sync",
            "started_at": "2026-06-05T10:00:00Z",
            "ended_at": "2026-06-05T10:30:00Z",
            "duration_seconds": "1800",
            "source": "chrome-extension",
        },
    )


def test_upload_creates_pending_job_and_persists_recording(tmp_path):
    app, worker = _app(tmp_path)
    with TestClient(app) as client:
        r = _upload(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    jobs = worker.jobs.list_jobs_for_user(1)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == "pending"
    assert job.source_file_id.startswith("chrome-extension:")
    assert job.source_file_name == "Weekly Sync"
    # Media + metadata sidecar landed in the shared recordings dir.
    rid = recording_id_from_source(job.source_file_id)
    media = resolve_recording_file(tmp_path / "recordings", rid)
    assert media is not None and media.read_bytes() == b"webm-audio-bytes"


def test_upload_disabled_without_configured_token(tmp_path):
    # Feature OFF: no legacy env token AND no per-user store. The endpoint
    # answers 503 (service disabled) before the auth check.
    app, worker = _app(tmp_path, token="", with_extension_store=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/recordings/upload",
            files={"file": ("rec.webm", b"x", "audio/webm")},
        )
    assert r.status_code == 503
    assert worker.jobs.list_jobs_for_user(1) == []


def test_upload_rejects_wrong_token(tmp_path):
    app, worker = _app(tmp_path)
    with TestClient(app) as client:
        r = _upload(client, token="wrong-token")
    assert r.status_code == 401
    assert worker.jobs.list_jobs_for_user(1) == []


def test_upload_never_logs_the_token(tmp_path, caplog):
    app, _ = _app(tmp_path)
    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            _upload(client)
    assert TOKEN not in caplog.text


def test_upload_rejects_empty_file(tmp_path):
    app, worker = _app(tmp_path)
    with TestClient(app) as client:
        r = _upload(client, content=b"")
    assert r.status_code == 400
    assert worker.jobs.list_jobs_for_user(1) == []


def test_upload_enforces_size_limit(tmp_path):
    app, worker = _app(tmp_path, max_mb=1)
    with TestClient(app) as client:
        r = _upload(client, content=b"x" * (1_200_000))  # > 1 MB
    assert r.status_code == 413
    assert worker.jobs.list_jobs_for_user(1) == []
    # The partial file is cleaned up, not left dangling.
    assert not any((tmp_path / "recordings").glob("*.webm")) if (tmp_path / "recordings").exists() else True


def test_upload_rejected_early_by_content_length(tmp_path):
    # Well over the limit + margin -> 413 from the ASGI guard before buffering.
    app, worker = _app(tmp_path, max_mb=1)
    with TestClient(app) as client:
        r = _upload(client, content=b"x" * (2_500_000))  # > 1 MB + 1 MiB margin
    assert r.status_code == 413
    assert worker.jobs.list_jobs_for_user(1) == []
    assert not (tmp_path / "recordings").exists() or not any(
        (tmp_path / "recordings").glob("*.webm")
    )
