from datetime import datetime, timezone

from app.webhooks import (
    JOB_COMPLETED,
    JOB_FAILED,
    WebhookNotifier,
    WebhookSettings,
    job_event_data,
)


class _Transport:
    """Records calls and returns/raises the next scripted outcome."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _settings(**over):
    base = dict(
        url="https://hook.example/test",
        events=frozenset({"job.completed", "job.failed"}),
        timeout_seconds=5,
        max_retries=2,
    )
    base.update(over)
    return WebhookSettings(**base)


def _notifier(transport, **over):
    return WebhookNotifier(
        _settings(**over),
        transport=transport,
        sleep=lambda *_: None,
        now=lambda: datetime(2026, 6, 5, tzinfo=timezone.utc),
    )


def test_settings_disabled_by_default():
    settings = WebhookSettings.from_env({})
    assert settings.enabled is False
    assert settings.events == frozenset({"job.completed", "job.failed"})


def test_settings_parse_url_and_events():
    settings = WebhookSettings.from_env(
        {"WEBHOOK_URL": "https://x", "WEBHOOK_EVENTS": "job.failed"}
    )
    assert settings.enabled is True
    assert settings.events == frozenset({"job.failed"})


def test_disabled_notifier_is_a_noop():
    transport = _Transport([200])
    notifier = WebhookNotifier(WebhookSettings.from_env({}), transport=transport)
    assert notifier.notify(JOB_COMPLETED, {"job_id": 1}) is False
    assert transport.calls == []


def test_unsubscribed_event_is_a_noop():
    transport = _Transport([200])
    notifier = _notifier(transport, events=frozenset({"job.failed"}))
    assert notifier.notify(JOB_COMPLETED, {"job_id": 1}) is False
    assert transport.calls == []


def test_delivered_on_2xx_with_envelope():
    transport = _Transport([200])
    assert _notifier(transport).notify(JOB_COMPLETED, {"job_id": 1}) is True
    _, payload, _ = transport.calls[0]
    assert payload["event"] == "job.completed"
    assert payload["data"]["job_id"] == 1
    assert "occurred_at" in payload


def test_retries_on_429_then_succeeds():
    transport = _Transport([429, 200])
    assert _notifier(transport).notify(JOB_FAILED, {"job_id": 2}) is True
    assert len(transport.calls) == 2


def test_gives_up_after_max_retries_on_5xx():
    transport = _Transport([500, 500, 500])
    assert _notifier(transport, max_retries=2).notify(JOB_COMPLETED, {"job_id": 3}) is False
    assert len(transport.calls) == 3


def test_transport_error_is_swallowed_then_retried():
    transport = _Transport([RuntimeError("boom"), 200])
    assert _notifier(transport).notify(JOB_COMPLETED, {"job_id": 4}) is True
    assert len(transport.calls) == 2


def test_payload_redacts_sensitive_fields():
    transport = _Transport([200])
    _notifier(transport).notify(JOB_COMPLETED, {"job_id": 5, "api_key": "sk-secret"})
    payload = transport.calls[0][1]
    assert payload["data"]["api_key"] == "***"
    assert "sk-secret" not in str(payload)


def test_job_event_data_is_secret_free():
    class _Job:
        id = 9
        user_id = 1
        status = "completed"
        source_file_id = "file-1"
        source_file_name = "meet.mp4"
        error_message = None

    data = job_event_data(_Job())
    assert data == {
        "job_id": 9,
        "user_id": 1,
        "status": "completed",
        "source_file_id": "file-1",
        "source_file_name": "meet.mp4",
        "error_message": None,
    }
