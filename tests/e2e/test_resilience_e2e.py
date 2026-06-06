from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from tests.e2e.helpers import (
    ADMIN_ID,
    build_app,
    deepgram_required_status,
    local_invalid_status,
    login,
    run_worker_once,
    seed_worker_ready,
)
from tests.support import FakeDeepgramClient, FakeDriveClient, drive_file


class BrokenQueue:
    """A queue whose Redis is down: enqueue raises and health() is False."""

    def enqueue(self, job_id):
        raise RuntimeError("redis down")

    def ensure_queued(self, job_id):
        return False

    def dequeue(self, timeout=0):
        return None

    def requeue(self, job_id):
        return None

    def acquire_global_lock(self, ttl_seconds):
        return None

    def release_global_lock(self, token):
        return None

    def queued_job_ids(self):
        return set()

    def health(self):
        return False


def test_run_once_blocked_without_deepgram_key_shows_friendly_error(tmp_path):
    worker = build_memory_repositories()
    seed_worker_ready(worker, deepgram_key=None)
    drive = FakeDriveClient(files=[drive_file("file-1", "meet.mp4")])
    app = build_app(
        tmp_path, worker=worker, transcription_status=deepgram_required_status(), drive=drive,
    )
    with TestClient(app) as client:
        login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        page = client.get("/jobs").text
    assert worker.jobs.list_jobs_for_user(ADMIN_ID) == []
    assert "Configure sua Deepgram API Key" in page


def test_redis_unavailable_keeps_job_pending_and_warns_and_degrades_ready(tmp_path):
    worker = build_memory_repositories()
    seed_worker_ready(worker)
    drive = FakeDriveClient(files=[drive_file("file-1", "meet.mp4")])
    app = build_app(
        tmp_path, worker=worker, queue=BrokenQueue(),
        transcription_status=deepgram_required_status(), drive=drive,
    )
    with TestClient(app) as client:
        login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        jobs_page = client.get("/jobs").text
        ready = client.get("/ready")
    # Postgres is the source of truth: the job is still created/pending.
    jobs = worker.jobs.list_jobs_for_user(ADMIN_ID)
    assert len(jobs) == 1 and jobs[0].status == "pending"
    assert "Fila indisponível" in jobs_page
    assert ready.status_code == 503
    assert ready.json()["checks"]["queue"]["ok"] is False


def test_local_invalid_shows_message_and_docs_link(tmp_path):
    worker = build_memory_repositories()
    app = build_app(
        tmp_path, worker=worker,
        transcription_status=local_invalid_status("https://docs.example/local"),
    )
    with TestClient(app) as client:
        login(client)
        page = client.get("/jobs").text
    assert "Modelo local inválido" in page
    assert "https://docs.example/local" in page


def test_failed_job_appears_in_ui_as_dead_letter(tmp_path):
    worker = build_memory_repositories()
    seed_worker_ready(worker)
    drive = FakeDriveClient(files=[drive_file("file-1", "meet.mp4")])
    app = build_app(
        tmp_path, worker=worker, transcription_status=deepgram_required_status(), drive=drive,
    )
    with TestClient(app) as client:
        login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        # The worker fails transcription (Deepgram raises); the job is marked failed.
        run_worker_once(tmp_path, worker, drive=drive, deepgram=FakeDeepgramClient(fail=True))
        job_id = worker.jobs.list_jobs_for_user(ADMIN_ID)[0].id
        jobs_page = client.get("/jobs").text
        detail = client.get(f"/jobs/{job_id}").text
    assert "badge-failed" in jobs_page
    assert "deepgram failed" in detail
    assert "Traceback" not in detail
