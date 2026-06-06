from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.config import parse_bool

DEFAULT_MODEL = "pyannote/speaker-diarization-3.1"
DEFAULT_ENGINE = "none"


@dataclass(frozen=True)
class DiarizationConfig:
    """Optional local speaker-diarization configuration from the environment.

    ``from_env`` NEVER raises on a bad value (an unknown engine just makes the
    config invalid later, decided by ``get_diarization_status``) so it can never
    crash worker startup. Diarization is OFF by default.

    SECURITY: ``auth_token`` is a secret. It must NEVER be logged or placed in any
    error/user message. The frozen-dataclass ``repr`` does expose it, so the
    config object itself must never be logged.
    """

    enabled: bool
    engine: str
    model: str
    auth_token: str | None
    required: bool
    min_speakers: int | None
    max_speakers: int | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DiarizationConfig":
        values = env if env is not None else os.environ
        return cls(
            enabled=_bool(values, "DIARIZATION_ENABLED", False),
            engine=_engine(values.get("DIARIZATION_ENGINE", DEFAULT_ENGINE)),
            model=values.get("DIARIZATION_MODEL", "").strip() or DEFAULT_MODEL,
            auth_token=values.get("DIARIZATION_AUTH_TOKEN", "").strip() or None,
            required=_bool(values, "DIARIZATION_REQUIRED", False),
            min_speakers=_opt_int(values, "DIARIZATION_MIN_SPEAKERS"),
            max_speakers=_opt_int(values, "DIARIZATION_MAX_SPEAKERS"),
        )

    @classmethod
    def disabled(cls) -> "DiarizationConfig":
        return cls.from_env({"DIARIZATION_ENABLED": "false"})


def _engine(raw: str) -> str:
    return raw.strip().lower()


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return parse_bool(raw)
    except ValueError:
        return default


def _opt_int(env: Mapping[str, str], key: str) -> int | None:
    raw = env.get(key, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None
