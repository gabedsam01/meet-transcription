from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# Events the worker can emit. Kept small and stable; the payload is secret-free.
DEFAULT_EVENTS = ("job.completed", "job.failed")


@dataclass(frozen=True)
class WebhookSettings:
    """Outbound webhook configuration (disabled unless ``WEBHOOK_URL`` is set)."""

    url: str | None
    events: frozenset[str]
    timeout_seconds: int
    max_retries: int

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WebhookSettings":
        values = env if env is not None else os.environ
        url = (values.get("WEBHOOK_URL", "") or "").strip() or None
        raw_events = (values.get("WEBHOOK_EVENTS", "") or "").strip()
        events = (
            tuple(e.strip() for e in raw_events.split(",") if e.strip())
            if raw_events
            else DEFAULT_EVENTS
        )
        return cls(
            url=url,
            events=frozenset(events),
            timeout_seconds=_positive_int(values, "WEBHOOK_TIMEOUT_SECONDS", 10),
            # 0 retries is valid (deliver once, no retry); negatives clamp to 0.
            max_retries=max(0, _int(values, "WEBHOOK_MAX_RETRIES", 2)),
        )


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    number = _int(env, key, default)
    if number <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return number
