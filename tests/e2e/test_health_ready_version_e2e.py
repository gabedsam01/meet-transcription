from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from tests.e2e.helpers import build_app, deepgram_required_status


def test_health_is_public_and_minimal(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_ready_with_worker_and_poll_mode(tmp_path):
    worker = build_memory_repositories()
    app = build_app(tmp_path, worker=worker)  # queue=None -> poll mode
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["migrations"]["ok"] is True
    assert body["checks"]["queue"]["ok"] is True


def test_ready_is_degraded_when_worker_backend_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_REPOSITORY_BACKEND", "does-not-exist")
    app = build_app(tmp_path)  # no worker injected -> backend resolution fails
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["ok"] is False


def test_ready_is_degraded_not_500_when_database_url_missing(tmp_path, monkeypatch):
    # Production lazy-builds the worker bundle; an unset DATABASE_URL raises
    # DatabaseConfigError. /ready must degrade to 503, never surface a 500.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("WORKER_REPOSITORY_BACKEND", raising=False)  # default 'postgres'
    app = build_app(tmp_path)  # no worker injected -> lazy build hits DatabaseConfigError
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"]["ok"] is False


def test_version_reports_build_and_provider_posture(tmp_path):
    app = build_app(tmp_path, transcription_status=deepgram_required_status())
    with TestClient(app) as client:
        response = client.get("/version")
    body = response.json()
    assert body["app"]
    assert "version" in body and "commit" in body
    assert body["providers"]["deepgram_required"] is True
    assert "queue_backend" in body["providers"]
    assert body["providers"]["summaries_enabled"] is False
