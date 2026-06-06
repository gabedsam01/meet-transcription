from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.config import parse_bool


@dataclass(frozen=True)
class AudioConfig:
    """Audio preprocessing configuration loaded from the environment.

    Like :class:`app.transcription.config.TranscriptionConfig`, ``from_env`` never
    raises on a *bad* value: an unparseable number/bool simply falls back to the
    safe default, so it can never crash worker startup. Preprocessing is **off by
    default** (``AUDIO_PREPROCESSING_ENABLED=false``).
    """

    enabled: bool
    target_sample_rate: int
    target_channels: int
    target_bitrate: int
    chunk_max_duration_seconds: int
    chunk_overlap_seconds: int
    max_inline_mb: int
    max_file_api_mb: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AudioConfig":
        values = env if env is not None else os.environ
        return cls(
            enabled=_bool(values, "AUDIO_PREPROCESSING_ENABLED", False),
            target_sample_rate=_int(values, "AUDIO_TARGET_SAMPLE_RATE", 16000),
            target_channels=_int(values, "AUDIO_TARGET_CHANNELS", 1),
            target_bitrate=_int(values, "AUDIO_TARGET_BITRATE", 24000),
            chunk_max_duration_seconds=_int(
                values, "AUDIO_CHUNK_MAX_DURATION_SECONDS", 900
            ),
            chunk_overlap_seconds=_int(values, "AUDIO_CHUNK_OVERLAP_SECONDS", 2),
            max_inline_mb=_int(values, "AUDIO_MAX_INLINE_MB", 70),
            max_file_api_mb=_int(values, "AUDIO_MAX_FILE_API_MB", 99),
        )

    @classmethod
    def disabled(cls) -> "AudioConfig":
        return cls.from_env({"AUDIO_PREPROCESSING_ENABLED": "false"})


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return parse_bool(raw)
    except ValueError:
        return default


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    # Overlap may legitimately be 0; allow non-negative for these knobs while
    # rejecting nonsense negatives.
    return value if value >= 0 else default
