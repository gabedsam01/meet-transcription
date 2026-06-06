from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.config import parse_bool


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    max_upload_mb: int
    free_tier_upload_mb: int | None = None
    preferred_format: str = "flac"
    supports_chunking: bool = True
    supports_url: bool = False
    supports_diarization: bool = False


def get_provider_capabilities(provider_name: str, config: AudioConfig) -> ProviderCapabilities:
    p = (provider_name or "").strip().lower()
    if p == "openrouter":
        return ProviderCapabilities(
            provider=provider_name,
            max_upload_mb=config.openrouter_max_upload_mb,
            preferred_format="flac",
            supports_chunking=True,
        )
    elif p == "gemini":
        return ProviderCapabilities(
            provider=provider_name,
            max_upload_mb=config.gemini_max_file_api_mb,
            preferred_format="flac",
            supports_chunking=True,
        )
    elif p == "groq":
        use_dev = os.environ.get("GROQ_USE_DEV_LIMIT", "").strip().lower() in ("1", "true", "yes", "y", "on")
        max_upload_mb = 100 if use_dev else config.groq_max_upload_mb
        return ProviderCapabilities(
            provider=provider_name,
            max_upload_mb=max_upload_mb,
            preferred_format="mp3",
            supports_chunking=True,
        )
    elif p == "deepgram":
        return ProviderCapabilities(
            provider=provider_name,
            max_upload_mb=2048,  # 2 GB
            preferred_format="flac",
            supports_chunking=True,
        )
    elif p == "assemblyai":
        return ProviderCapabilities(
            provider=provider_name,
            max_upload_mb=config.provider_limit_default_mb,
            preferred_format="flac",
            supports_chunking=True,
        )
    else:
        # local or unknown
        return ProviderCapabilities(
            provider=provider_name,
            max_upload_mb=999999,
            preferred_format="wav",
            supports_chunking=False,
        )


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
    compression_enabled: bool
    compression_target_mb: int
    cloud_chunk_target_mb: int
    provider_limit_default_mb: int
    openrouter_max_upload_mb: int
    gemini_max_file_api_mb: int
    groq_max_upload_mb: int

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
            compression_enabled=_bool(values, "AUDIO_COMPRESSION_ENABLED", True),
            compression_target_mb=_int(values, "AUDIO_COMPRESSION_TARGET_MB", 99),
            cloud_chunk_target_mb=_int(values, "AUDIO_CLOUD_CHUNK_TARGET_MB", 24),
            provider_limit_default_mb=_int(values, "AUDIO_PROVIDER_LIMIT_DEFAULT_MB", 99),
            openrouter_max_upload_mb=_int(values, "OPENROUTER_MAX_UPLOAD_MB", 99),
            gemini_max_file_api_mb=_int(values, "GEMINI_MAX_FILE_API_MB", 99),
            groq_max_upload_mb=_int(values, "GROQ_MAX_UPLOAD_MB", 25),
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
