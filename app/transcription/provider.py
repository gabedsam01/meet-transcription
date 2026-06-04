from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.transcription.config import TranscriptionConfig
from app.transcription.local_validation import (
    ValidationProbes,
    validate_local_config,
)


@dataclass(frozen=True)
class TranscriptionResult:
    """What a provider returns to the worker.

    ``text`` is the human-readable transcript stored in ``transcripts.text`` and
    served by the TXT download. ``payload`` is the normalized schema stored in
    ``transcripts.transcript_json``.
    """

    text: str
    payload: dict[str, Any]


@runtime_checkable
class TranscriptionProvider(Protocol):
    def transcribe(
        self, source_path: str | Path, *, original_name: str, file_id: str
    ) -> TranscriptionResult: ...


@dataclass(frozen=True)
class ProviderStatus:
    """Resolved transcription posture for the current configuration.

    - ``local_valid`` — a local engine is enabled and fully configured.
    - ``deepgram_required`` — a per-user Deepgram key is needed to transcribe
      (local disabled, or local enabled-but-invalid). There is no silent fallback:
      an invalid local config surfaces a message and the doc link.
    """

    enabled: bool
    local_valid: bool
    deepgram_required: bool
    summary: str | None
    message: str
    doc_url: str | None
    # Technical reason a local engine is invalid (for logs/diagnostics), distinct
    # from the friendly ``message`` shown in the UI. None when local is valid.
    reason: str | None = None


def get_transcription_provider_status(
    config: TranscriptionConfig, *, probes: ValidationProbes | None = None
) -> ProviderStatus:
    if not config.enabled:
        return ProviderStatus(
            enabled=False,
            local_valid=False,
            deepgram_required=True,
            summary=None,
            message="Transcrição local desativada; Deepgram é obrigatório.",
            doc_url=None,
        )

    validation = validate_local_config(config, probes)
    if validation.valid:
        return ProviderStatus(
            enabled=True,
            local_valid=True,
            deepgram_required=False,
            summary=validation.summary,
            message=f"Modelo local ativo: {validation.summary}",
            doc_url=config.doc_url,
        )

    return ProviderStatus(
        enabled=True,
        local_valid=False,
        deepgram_required=True,
        summary=None,
        message="Modelo local inválido. Consulte a documentação de modelos locais.",
        doc_url=config.doc_url,
        reason=validation.reason,
    )
