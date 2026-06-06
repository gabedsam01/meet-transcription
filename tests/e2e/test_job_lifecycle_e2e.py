import json

from fastapi.testclient import TestClient

from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.repositories.memory import build_memory_repositories
from tests.e2e.helpers import (
    ADMIN_ID,
    build_app,
    deepgram_required_status,
    login,
    run_worker_once,
    seed_worker_ready,
)
from tests.support import FakeDeepgramClient, FakeDriveClient, drive_file


def test_run_once_enqueues_then_worker_completes_then_download_and_export(tmp_path):
    worker = build_memory_repositories()
    seed_worker_ready(worker)
    queue = InMemoryTranscriptionQueue()
    drive = FakeDriveClient(files=[drive_file("file-1", "Weekly Sync.mp4")])
    app = build_app(
        tmp_path, worker=worker, queue=queue,
        transcription_status=deepgram_required_status(), drive=drive,
    )
    with TestClient(app) as client:
        login(client)

        # 1) Auto-poll: run-once scans Drive and enqueues a single pending job.
        client.post("/jobs/run-once", follow_redirects=False)
        jobs = worker.jobs.list_jobs_for_user(ADMIN_ID)
        assert len(jobs) == 1 and jobs[0].status == "pending"
        job_id = jobs[0].id
        assert queue.queued_job_ids() == {job_id}

        # 2) The fake worker processes the queued job to completion.
        assert run_worker_once(tmp_path, worker, drive=drive, deepgram=FakeDeepgramClient()) == 1
        page = client.get("/jobs").text
        assert "badge-completed" in page

        # 3) Download TXT (default) and the alternate export formats.
        txt = client.get(f"/jobs/{job_id}/download")
        assert txt.status_code == 200
        assert txt.headers["content-type"].startswith("text/plain")
        assert "Ola mundo." in txt.text

        srt = client.get(f"/jobs/{job_id}/download", params={"format": "srt"})
        assert srt.status_code == 200
        assert srt.headers["content-type"].startswith("application/x-subrip")
        assert "-->" in srt.text

        vtt = client.get(f"/jobs/{job_id}/download", params={"format": "vtt"})
        assert vtt.status_code == 200
        assert vtt.text.startswith("WEBVTT")

        js = client.get(f"/jobs/{job_id}/download", params={"format": "json"})
        assert js.status_code == 200
        assert json.loads(js.text)["provider"] == "deepgram"

        md = client.get(f"/jobs/{job_id}/download", params={"format": "md"})
        assert md.status_code == 200
        assert md.text.startswith("#")

        bad = client.get(f"/jobs/{job_id}/download", params={"format": "pdf"})
        assert bad.status_code == 400


def test_job_detail_lists_export_links_for_completed_job(tmp_path):
    worker = build_memory_repositories()
    seed_worker_ready(worker)
    drive = FakeDriveClient(files=[drive_file("file-1", "meet.mp4")])
    app = build_app(
        tmp_path, worker=worker, transcription_status=deepgram_required_status(), drive=drive,
    )
    with TestClient(app) as client:
        login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        run_worker_once(tmp_path, worker, drive=drive, deepgram=FakeDeepgramClient())
        job_id = worker.jobs.list_jobs_for_user(ADMIN_ID)[0].id
        detail = client.get(f"/jobs/{job_id}").text
    assert "Formatos adicionais" in detail
    assert "?format=srt" in detail
    assert "?format=json" in detail
