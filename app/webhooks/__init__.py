"""Optional outbound webhooks for job lifecycle events.

A deployment can set ``WEBHOOK_URL`` (+ ``WEBHOOK_EVENTS``) to receive a POST when
a job completes or fails. Delivery is **best-effort and never blocks or fails a
job**: the worker fires the webhook after the job already reached its terminal
state, the notifier swallows every error, and the payload is **secret-free** (no
tokens, keys, or credentials — see :func:`app.observability.redact`).

See ``documentation/35-webhooks.md``.
"""

from __future__ import annotations

from app.webhooks.config import DEFAULT_EVENTS, WebhookSettings
from app.webhooks.notifier import (
    JOB_COMPLETED,
    JOB_FAILED,
    RETRYABLE_STATUS,
    WebhookNotifier,
    job_event_data,
)

__all__ = [
    "DEFAULT_EVENTS",
    "WebhookSettings",
    "WebhookNotifier",
    "JOB_COMPLETED",
    "JOB_FAILED",
    "RETRYABLE_STATUS",
    "job_event_data",
]
