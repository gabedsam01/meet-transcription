from __future__ import annotations

import logging
from typing import Callable

from app.errors import LocalTranscriptionUnavailableError
from app.transcription.config import TranscriptionConfig
from app.transcription.local_validation import ValidationProbes
from app.transcription.provider import (
    ProviderStatus,
    TranscriptionProvider,
    get_transcription_provider_status,
)

LOGGER = logging.getLogger(__name__)

# Back-compat alias: the canonical error now lives in app.errors.
LocalTranscriptionUnavailable = LocalTranscriptionUnavailableError


def build_local_provider(config: TranscriptionConfig) -> TranscriptionProvider:
    """Instantiate the local provider for the configured engine.

    Imported lazily to avoid pulling whisper.cpp/faster-whisper imports into modules
    that only need the abstract types.
    """
    if config.engine == "whisper-cpp":
        from app.transcription.whisper_cpp_provider import WhisperCppProvider

        return WhisperCppProvider(config)
    from app.transcription.faster_whisper_provider import FasterWhisperProvider

    return FasterWhisperProvider(config)


def resolve_provider(
    config: TranscriptionConfig,
    *,
    has_deepgram_key: bool,
    build_local_provider: Callable[[TranscriptionConfig], TranscriptionProvider],
    build_deepgram_provider: Callable[[], TranscriptionProvider],
    probes: ValidationProbes | None = None,
) -> tuple[TranscriptionProvider, ProviderStatus]:
    """Pick the transcription provider per the product rule.

    1. Valid local engine -> local provider (no Deepgram key needed).
    2. Otherwise (local disabled or invalid) -> Deepgram, requiring a key.
    3. No key available in case 2 -> raise ``LocalTranscriptionUnavailable`` with a
       Deepgram-mentioning, docs-linked message. Never a silent fallback.
    """
    status = get_transcription_provider_status(config, probes=probes)
    if status.local_valid:
        return build_local_provider(config), status
    if not has_deepgram_key:
        message = _unavailable_message(status)
        raise LocalTranscriptionUnavailableError(message, user_message=message)
    if status.enabled:
        # Local was requested but is invalid; we fall back to Deepgram and say so.
        LOGGER.info(
            "Local transcription invalid; requiring Deepgram: reason=%s",
            status.reason or status.message,
        )
    return build_deepgram_provider(), status


def _unavailable_message(status: ProviderStatus) -> str:
    message = (
        f"Não há provedor de transcrição disponível: {status.message} "
        "Configure uma Deepgram API Key"
    )
    if status.doc_url:
        message += f" ou ajuste o modelo local ({status.doc_url})"
    return message + "."
