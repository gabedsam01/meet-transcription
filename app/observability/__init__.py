"""Structured, secret-free logging for the web and worker.

Goals (see ``documentation/34-observability.md``):

- One log line per meaningful event with consistent fields: ``event``,
  ``job_id``, ``user_id``, ``provider``, ``duration_seconds``, ``error_code``,
  ``retryable``.
- **Never log a secret.** :func:`redact` strips any field whose name looks like a
  token / key / password / credential before it is ever formatted, so a careless
  ``log_event(..., api_key=key)`` cannot leak.
- Two output formats, selected by ``LOG_FORMAT`` (``text`` default, or ``json``).
  ``log_event`` works the same either way: the fields go on the record as
  ``extra`` (used by the JSON formatter) and are mirrored into the text message.

This module owns *what* we log; ``app/logger.py`` owns *how* the root logger is
configured and simply delegates here.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping

REDACTED = "***"

# A field is redacted when any of these substrings appears in its (lower-cased)
# name. Deliberately broad — it is always safer to redact one field too many.
_SENSITIVE_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "key",  # also covers api_key / deepgram_key / app_secret_key
    "authorization",
    "auth",
    "credential",
    "cookie",
    "session",
    "fernet",
)

_EVENT_LOGGER = "app.events"
# Standard LogRecord attributes; everything else a caller attaches is an event field.
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime", "taskName"}


def _is_sensitive(name: str) -> bool:
    low = name.lower()
    return any(hint in low for hint in _SENSITIVE_HINTS)


def _redact_value(key: str, value: Any) -> Any:
    if value not in (None, "") and _is_sensitive(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # Items inherit the parent key (already known non-sensitive here); nested
        # mappings are still masked by their own keys.
        return [_redact_value(key, item) for item in value]
    return value


def redact(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of ``fields`` with sensitive-looking values masked.

    Masks by field name (substring match against the sensitive hints) and recurses
    into nested mappings/sequences, so a secret under a nested sensitive key is
    masked too. Name-based masking is the first line of defense — callers should
    still avoid putting raw secrets/exception text in log or webhook fields.
    """
    return {key: _redact_value(key, value) for key, value in fields.items()}


def _fmt_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    text = str(value)
    return f'"{text}"' if (" " in text or "=" in text) else text


def log_event(
    event: str,
    *,
    level: int = logging.INFO,
    logger: logging.Logger | None = None,
    **fields: Any,
) -> None:
    """Emit one structured, secret-free log line for ``event``.

    Extra keyword fields are redacted, mirrored into the human-readable message,
    and attached as ``extra`` so the JSON formatter can render them as real keys.
    """
    log = logger or logging.getLogger(_EVENT_LOGGER)
    clean = redact(fields)
    parts = [f"event={event}"] + [f"{k}={_fmt_value(v)}" for k, v in clean.items()]
    log.log(level, " ".join(parts), extra={"event": event, "event_fields": clean})


class JsonLogFormatter(logging.Formatter):
    """Render each record as a single JSON object (one line).

    Includes the standard fields plus any structured event fields, and redacts
    defensively a second time so even ad-hoc ``logger.info(..., extra=...)`` calls
    cannot leak a secret.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = event
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, Mapping):
            payload.update(redact(fields))
        else:
            # Pick up any non-reserved attributes attached via ``extra=``.
            extras = {
                k: v for k, v in record.__dict__.items()
                if k not in _RESERVED and k not in ("event", "event_fields")
            }
            if extras:
                payload.update(redact(extras))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def resolve_log_format(env: Mapping[str, str] | None = None) -> str:
    """``json`` or ``text`` (default) from ``LOG_FORMAT``; anything else → text."""
    values = env if env is not None else os.environ
    fmt = (values.get("LOG_FORMAT", "text") or "text").strip().lower()
    return "json" if fmt == "json" else "text"


def configure_logging(level: int = logging.INFO, fmt: str | None = None) -> None:
    """Install a single root handler with the text or JSON formatter (idempotent).

    Safe to call repeatedly (the web app factory and worker both call it): it
    reuses one handler and just updates the formatter, so handlers never stack up.
    """
    chosen = fmt or resolve_log_format()
    root = logging.getLogger()
    root.setLevel(level)

    handler = getattr(configure_logging, "_handler", None)
    if handler is None or handler not in root.handlers:
        handler = logging.StreamHandler()
        # Drop any handlers basicConfig or a prior run installed, then own the root.
        for existing in list(root.handlers):
            root.removeHandler(existing)
        root.addHandler(handler)
        configure_logging._handler = handler  # type: ignore[attr-defined]

    if chosen == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )


__all__ = [
    "REDACTED",
    "redact",
    "log_event",
    "JsonLogFormatter",
    "resolve_log_format",
    "configure_logging",
]
