from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.models import Settings as WorkerSettings
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.repositories.memory import build_memory_repositories
from app.webhooks import WebhookNotifier, WebhookSettings
from tests.e2e.helpers import (
    ADMIN_ID,
    build_app,
    deepgram_required_status,
    login,
    now,
    run_worker_once,
    seed_worker_ready,
)
from tests.support import FakeDeepgramClient, FakeDriveClient, drive_file


class _Transport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append(payload)
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _notifier(transport, events=("job.completed", "job.failed")):
    settings = WebhookSettings(
        url="https://hook.example/test", events=frozenset(events),
        timeout_seconds=5, max_retries=2,
    )
    return WebhookNotifier(
        settings, transport=transport, sleep=lambda *_: None,
        now=lambda: datetime(2026, 6, 5, tzinfo=timezone.utc),
    )


def _seed_pending(worker):
    seed_worker_ready(worker)
    return worker.jobs.create_job(ADMIN_ID, "file-1", "meet.mp4", now())


def test_worker_fires_completed_webhook_and_retries_on_429(tmp_path):
    worker = build_memory_repositories()
    _seed_pending(worker)
    transport = _Transport([429, 200])  # rate limited once, then accepted
    run_worker_once(
        tmp_path, worker, drive=FakeDriveClient(), deepgram=FakeDeepgramClient(),
        webhook_notifier=_notifier(transport),
    )
    assert len(transport.calls) == 2  # retried after the 429
    last = transport.calls[-1]
    assert last["event"] == "job.completed"
    assert last["data"]["status"] == "completed"
    assert last["data"]["error_code"] is None
    assert "occurred_at" in last


def test_failed_webhook_for_unexpected_error_is_secret_free(tmp_path):
    # A non-AppError failure (DeepgramClient raises RuntimeError("deepgram failed")).
    # The webhook must NOT leak the raw exception text — only a generic message + code.
    worker = build_memory_repositories()
    _seed_pending(worker)
    transport = _Transport([200])
    # job_max_attempts=1 makes the unexpected error terminal in one pass, so the
    # job.failed webhook fires immediately (retry/backoff is unit-tested elsewhere).
    run_worker_once(
        tmp_path, worker, drive=FakeDriveClient(),
        deepgram=FakeDeepgramClient(fail=True), webhook_notifier=_notifier(transport),
        job_max_attempts=1,
    )
    data = transport.calls[-1]["data"]
    assert transport.calls[-1]["event"] == "job.failed"
    assert data["error_code"] == "internal_error"
    assert data["error_message"] == "Falha no processamento da transcrição."
    # The raw exception text must never reach the external webhook.
    assert "deepgram failed" not in str(transport.calls[-1])


def test_failed_webhook_forwards_curated_apperror_message(tmp_path):
    # A mapped AppError (missing Google token) forwards its secret-free user_message.
    worker = build_memory_repositories()
    worker.settings.set(WorkerSettings(ADMIN_ID, "src", "dst", False, "user-dg-key"))
    worker.jobs.create_job(ADMIN_ID, "file-1", "meet.mp4", now())  # no google token seeded
    transport = _Transport([200])
    run_worker_once(
        tmp_path, worker, drive=FakeDriveClient(), deepgram=FakeDeepgramClient(),
        webhook_notifier=_notifier(transport),
    )
    data = transport.calls[-1]["data"]
    assert data["error_code"] == "google_not_connected"
    assert "Conecte sua conta Google" in data["error_message"]


def test_webhook_failure_never_blocks_job_completion(tmp_path):
    worker = build_memory_repositories()
    job = _seed_pending(worker)
    transport = _Transport([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    run_worker_once(
        tmp_path, worker, drive=FakeDriveClient(), deepgram=FakeDeepgramClient(),
        webhook_notifier=_notifier(transport),
    )
    # Every webhook attempt failed, yet the job still reached its terminal state.
    assert worker.jobs.get_job(job.id).status == "completed"


def test_disabled_webhooks_do_not_call_transport(tmp_path):
    worker = build_memory_repositories()
    _seed_pending(worker)
    transport = _Transport([200])
    notifier = WebhookNotifier(WebhookSettings.from_env({}), transport=transport)
    run_worker_once(
        tmp_path, worker, drive=FakeDriveClient(), deepgram=FakeDeepgramClient(),
        webhook_notifier=notifier,
    )
    assert transport.calls == []


def test_webhook_fires_for_a_job_through_the_full_run_once_lifecycle(tmp_path):
    # True end-to-end: log in via the web app, POST /jobs/run-once (which enqueues),
    # then run the worker — the webhook fires for that lifecycle-created job.
    worker = build_memory_repositories()
    seed_worker_ready(worker)
    queue = InMemoryTranscriptionQueue()
    drive = FakeDriveClient(files=[drive_file("file-1", "meet.mp4")])
    app = build_app(
        tmp_path, worker=worker, queue=queue,
        transcription_status=deepgram_required_status(), drive=drive,
    )
    transport = _Transport([200])
    with TestClient(app) as client:
        login(client)
        client.post("/jobs/run-once", follow_redirects=False)
        assert queue.queued_job_ids(), "run-once should enqueue the pending job"
        run_worker_once(
            tmp_path, worker, drive=drive, deepgram=FakeDeepgramClient(),
            webhook_notifier=_notifier(transport),
        )
    assert transport.calls[-1]["event"] == "job.completed"
    assert transport.calls[-1]["data"]["status"] == "completed"
