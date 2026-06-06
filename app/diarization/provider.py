from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from app.diarization.config import DiarizationConfig

# Engines accepted by the diarization layer. "none" is an explicit no-op.
ALLOWED_ENGINES = ("none", "pyannote")

# The pyannote.audio package providing the diarization pipeline.
_PYANNOTE_MODULE = "pyannote.audio"

_DISABLED_MESSAGE = "Diarização desativada."


@dataclass(frozen=True)
class SpeakerTurn:
    """A single diarized speaker turn: ``speaker`` is active during [start, end)."""

    start: float
    end: float
    speaker: str


@runtime_checkable
class DiarizationProvider(Protocol):
    def diarize(
        self,
        audio_path: str | Path,
        *,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> list[SpeakerTurn]: ...


@dataclass(frozen=True)
class DiarizationStatus:
    """Resolved diarization posture for the current configuration.

    - ``valid`` — diarization is enabled and a usable engine is fully configured.
    - ``message`` — friendly, secret-free sentence safe to show in the UI.
    - ``reason`` — technical detail for logs/diagnostics (never a secret), or None.
    """

    enabled: bool
    valid: bool
    required: bool
    engine: str
    message: str
    reason: str | None = None


@dataclass(frozen=True)
class DiarizationProbes:
    """Side-effecting checks, injected so tests need no real package installed."""

    module_available: Callable[[str], bool]


def _module_available(name: str) -> bool:
    # ``find_spec`` raises ModuleNotFoundError for a dotted name whose PARENT
    # package is missing (e.g. "pyannote.audio" when "pyannote" is absent), so we
    # treat any failure as "not available" rather than letting it propagate.
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def default_probes() -> DiarizationProbes:
    return DiarizationProbes(module_available=_module_available)


def get_diarization_status(
    config: DiarizationConfig, *, probes: DiarizationProbes | None = None
) -> DiarizationStatus:
    probes = probes or default_probes()

    # Disabled, or an explicit no-op engine -> a valid-but-inactive posture.
    if not config.enabled or config.engine == "none":
        return DiarizationStatus(
            enabled=config.enabled,
            valid=False,
            required=config.required,
            engine=config.engine,
            message=_DISABLED_MESSAGE,
        )

    if config.engine not in ALLOWED_ENGINES:
        return DiarizationStatus(
            enabled=True,
            valid=False,
            required=config.required,
            engine=config.engine,
            message="Engine de diarização não suportado.",
            reason=f"Engine de diarização não suportado: {config.engine!r}.",
        )

    # engine == "pyannote"
    if not probes.module_available(_PYANNOTE_MODULE):
        return DiarizationStatus(
            enabled=True,
            valid=False,
            required=config.required,
            engine=config.engine,
            message="Diarização indisponível: pacote pyannote.audio não instalado.",
            reason=(
                "O pacote pyannote.audio não está instalado nesta imagem "
                "(INSTALL_PYANNOTE=true)."
            ),
        )
    if not config.auth_token:
        return DiarizationStatus(
            enabled=True,
            valid=False,
            required=config.required,
            engine=config.engine,
            message="Diarização indisponível: token de acesso ausente.",
            reason="Token de acesso da diarização ausente (DIARIZATION_AUTH_TOKEN).",
        )

    return DiarizationStatus(
        enabled=True,
        valid=True,
        required=config.required,
        engine=config.engine,
        message=f"Diarização ativa: {config.engine}.",
    )


def build_diarization_provider(config: DiarizationConfig) -> DiarizationProvider:
    if config.enabled and config.engine == "pyannote":
        from app.diarization.pyannote_provider import (  # lazy: keeps import cheap
            PyannoteDiarizationProvider,
        )

        return PyannoteDiarizationProvider(config)

    from app.diarization.none_provider import NoneDiarizationProvider

    return NoneDiarizationProvider()
