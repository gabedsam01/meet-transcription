"""Pluggable transcription providers (Deepgram + local CPU engines).

All engines normalize into one internal schema (see ``normalizer``) so the worker
and the TXT download are engine-agnostic. ``get_transcription_provider_status``
encodes the product rule: use a valid local engine when enabled, otherwise fall
back to a per-user Deepgram key — never silently, always with a clear message.
"""

from __future__ import annotations

from app.transcription.config import TranscriptionConfig
from app.transcription.provider import (
    ProviderStatus,
    TranscriptionProvider,
    TranscriptionResult,
    get_transcription_provider_status,
)

__all__ = [
    "TranscriptionConfig",
    "TranscriptionProvider",
    "TranscriptionResult",
    "ProviderStatus",
    "get_transcription_provider_status",
]
