from fastapi.testclient import TestClient

from app.core.models import GoogleToken as WorkerGoogleToken, Settings as WorkerSettings
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.repositories.memory import build_memory_repositories
from app.transcription.provider import ProviderStatus
from app.web.config import WebSettings
from app.web.main import create_app
from tests.fakes import build_fake_repositories
from tests.support import FakeDriveClient, drive_file


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
            "DATABASE_URL": "postgresql://test",
            "TMP_DIR": str(tmp_path / "tmp"),
        }
    )


def _status(**over):
    base = dict(
        enabled=True,
        local_valid=False,
        deepgram_required=True,
        summary=None,
        message="msg",
        doc_url=None,
    )
    base.update(over)
    return ProviderStatus(**base)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)
    assert r.status_code in {302, 303}


def _seed_worker(deepgram_key, files=None):
    worker = build_memory_repositories()
    worker.settings.set(WorkerSettings(1, "src", "dst", False, deepgram_key))
    worker.google_tokens.set(1, WorkerGoogleToken(access_token="a", token_uri="u", client_id="c"))
    return worker


def _wire_drive(app, files):
    drive = FakeDriveClient(files=list(files or []))
    app.state.build_drive_client = lambda credentials, src, dst: drive
    app.state.credentials_from_token = lambda token: object()
    return drive


def test_run_once_enqueues_job_id_when_ready(tmp_path):
    worker = _seed_worker("dg-key")
    queue = InMemoryTranscriptionQueue()
    app = create_app(
        _settings(tmp_path), repositories=build_fake_repositories(),
        worker_repositories=worker, queue=queue,
    )
    _wire_drive(app, [drive_file("file-1", "m.mp4")])
    with TestClient(app) as client:
        _login(client)
        client.post("/jobs/run-once", follow_redirects=False)
    jobs = worker.jobs.list_jobs_for_user(1)
    assert len(jobs) == 1
    assert queue.queued_job_ids() == {jobs[0].id}  # job id was enqueued to Redis


def test_run_once_allowed_without_deepgram_when_local_valid(tmp_path):
    worker = _seed_worker(deepgram_key=None)  # no key on file
    status = _status(
        local_valid=True, deepgram_required=False,
        summary="faster-whisper small int8",
        message="Modelo local ativo: faster-whisper small int8",
    )
    app = create_app(
        _settings(tmp_path), repositories=build_fake_repositories(),
        worker_repositories=worker, transcription_status=status,
    )
    _wire_drive(app, [drive_file("file-1", "m.mp4")])
    with TestClient(app) as client:
        _login(client)
        client.post("/jobs/run-once", follow_redirects=False)
    jobs = worker.jobs.list_jobs_for_user(1)
    assert len(jobs) == 1 and jobs[0].status == "pending"


def test_run_once_blocks_without_deepgram_when_local_invalid(tmp_path):
    worker = _seed_worker(deepgram_key=None)
    status = _status(
        local_valid=False, deepgram_required=True,
        message="Modelo local inválido. Consulte a documentação de modelos locais.",
        doc_url="https://docs/local",
    )
    app = create_app(
        _settings(tmp_path), repositories=build_fake_repositories(),
        worker_repositories=worker, transcription_status=status,
    )
    _wire_drive(app, [drive_file("file-1", "m.mp4")])
    with TestClient(app) as client:
        _login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        page = client.get("/jobs").text
    assert worker.jobs.list_jobs_for_user(1) == []  # blocked: nothing enqueued
    assert "Configure sua Deepgram API Key" in page


def test_jobs_page_shows_local_invalid_alert_with_doc_link(tmp_path):
    status = _status(
        local_valid=False, deepgram_required=True,
        message="Modelo local inválido. Consulte a documentação de modelos locais.",
        doc_url="https://docs/local",
    )
    app = create_app(
        _settings(tmp_path), repositories=build_fake_repositories(),
        worker_repositories=build_memory_repositories(), transcription_status=status,
    )
    with TestClient(app) as client:
        _login(client)
        page = client.get("/jobs").text
    assert "Modelo local inválido" in page
    assert "https://docs/local" in page


def test_dashboard_shows_local_active(tmp_path):
    status = _status(
        local_valid=True, deepgram_required=False,
        summary="whisper.cpp small q4_0",
        message="Modelo local ativo: whisper.cpp small q4_0",
    )
    app = create_app(
        _settings(tmp_path), repositories=build_fake_repositories(),
        worker_repositories=build_memory_repositories(), transcription_status=status,
    )
    with TestClient(app) as client:
        _login(client)
        page = client.get("/").text
    assert "Modelo local ativo: whisper.cpp small q4_0" in page
