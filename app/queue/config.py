from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

VALID_BACKENDS = ("none", "memory", "redis")


@dataclass(frozen=True)
class QueueSettings:
    """Queue/lock configuration shared by web (enqueue) and worker (consume).

    ``backend`` defaults to ``none`` so an un-configured worker keeps the legacy
    poll loop and existing behavior. Production sets ``QUEUE_BACKEND=redis``.
    """

    backend: str
    redis_url: str
    queue_name: str
    global_lock_ttl_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "QueueSettings":
        values = env if env is not None else os.environ
        backend = (values.get("QUEUE_BACKEND", "none").strip().lower() or "none")
        if backend not in VALID_BACKENDS:
            raise ValueError(
                f"Unknown QUEUE_BACKEND={backend!r}; use one of {VALID_BACKENDS}."
            )
        return cls(
            backend=backend,
            redis_url=values.get("REDIS_URL", "").strip() or "redis://redis:6379/0",
            queue_name=values.get("QUEUE_NAME", "").strip() or "transcription",
            global_lock_ttl_seconds=_positive_int(
                values, "TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS", 14400
            ),
        )


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return number
