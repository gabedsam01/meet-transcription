from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.observability import log_event, redact
from app.webhooks.config import WebhookSettings

LOGGER = logging.getLogger(__name__)

JOB_COMPLETED = "job.completed"
JOB_FAILED = "job.failed"

# Transient HTTP statuses worth retrying. 429 = rate limited; 5xx = server-side.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: ``transport(url, payload, timeout) -> int`` returns the HTTP status code.
Transport = Callable[[str, dict[str, Any], int], int]


def _requests_transport(url: str, payload: dict[str, Any], timeout: int) -> int:
    import requests

    response = requests.post(url, json=payload, timeout=timeout)
    return response.status_code


def job_event_data(job: Any, *, status: str | None = None) -> dict[str, Any]:
    """Build a secret-free payload describing a job for a webhook.

    Only stable, non-sensitive fields: ids, status, the source filename, and the
    friendly ``error_message`` (never a token, key, or traceback).
    """
    return {
        "job_id": getattr(job, "id", None),
        "user_id": getattr(job, "user_id", None),
        "status": status or getattr(job, "status", None),
        "source_file_id": getattr(job, "source_file_id", None),
        "source_file_name": getattr(job, "source_file_name", None),
        "error_message": getattr(job, "error_message", None),
    }


class WebhookNotifier:
    """Best-effort webhook delivery. Never raises; never blocks job completion.

    Retries transient failures (network error / 429 / 5xx) up to
    ``settings.max_retries`` extra attempts. Every outcome is logged via
    :func:`app.observability.log_event` with secret-free fields.
    """

    def __init__(
        self,
        settings: WebhookSettings,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], datetime] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport or _requests_transport
        self._sleep = sleep or time.sleep
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._log = logger or LOGGER

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def notify(self, event: str, data: dict[str, Any]) -> bool:
        """Deliver ``event`` with ``data``. Returns True only if delivered (2xx).

        A no-op (returns False) when webhooks are disabled or ``event`` is not in
        ``WEBHOOK_EVENTS`` — so callers never need to guard.
        """
        if not self.settings.enabled or event not in self.settings.events:
            return False
        payload = {
            "event": event,
            "occurred_at": self._now().isoformat(),
            "data": redact(data),
        }
        return self._deliver(event, payload)

    def notify_job(self, event: str, job: Any, *, status: str | None = None) -> bool:
        return self.notify(event, job_event_data(job, status=status))

    def _deliver(self, event: str, payload: dict[str, Any]) -> bool:
        attempts = self.settings.max_retries + 1
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                status = self._transport(
                    self.settings.url, payload, self.settings.timeout_seconds
                )
            except Exception as exc:  # noqa: BLE001 - delivery must never raise to the worker.
                if last:
                    log_event(
                        "webhook.failed", logger=self._log, level=logging.WARNING,
                        hook_event=event, attempt=attempt, reason="transport_error",
                        error=str(exc),
                    )
                    return False
                log_event(
                    "webhook.retry", logger=self._log, level=logging.WARNING,
                    hook_event=event, attempt=attempt, reason="transport_error",
                )
                self._sleep(self._backoff(attempt))
                continue

            if 200 <= status < 300:
                log_event(
                    "webhook.delivered", logger=self._log,
                    hook_event=event, attempt=attempt, status=status,
                )
                return True
            if status in RETRYABLE_STATUS and not last:
                log_event(
                    "webhook.retry", logger=self._log, level=logging.WARNING,
                    hook_event=event, attempt=attempt, status=status,
                )
                self._sleep(self._backoff(attempt))
                continue
            log_event(
                "webhook.failed", logger=self._log, level=logging.WARNING,
                hook_event=event, attempt=attempt, status=status,
            )
            return False
        return False

    @staticmethod
    def _backoff(attempt: int) -> float:
        # Gentle exponential backoff, capped; tests inject a no-op sleep.
        return min(0.5 * (2 ** (attempt - 1)), 5.0)


__all__ = [
    "WebhookNotifier",
    "JOB_COMPLETED",
    "JOB_FAILED",
    "RETRYABLE_STATUS",
    "job_event_data",
]
