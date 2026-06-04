from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Callable

from app.transcription.config import (
    ALLOWED_COMPUTE_TYPES,
    ALLOWED_ENGINES,
    ALLOWED_MODELS,
    ALLOWED_QUANTIZATIONS,
    TranscriptionConfig,
)


@dataclass(frozen=True)
class LocalValidation:
    valid: bool
    summary: str | None = None  # e.g. "faster-whisper small int8"
    reason: str | None = None  # human-friendly explanation when invalid


@dataclass(frozen=True)
class ValidationProbes:
    """Side-effecting checks, injected so tests need no real package/file/binary."""

    module_available: Callable[[str], bool]
    path_exists: Callable[[str], bool]
    is_executable: Callable[[str], bool]


def default_probes() -> ValidationProbes:
    return ValidationProbes(
        module_available=lambda name: importlib.util.find_spec(name) is not None,
        path_exists=os.path.exists,
        is_executable=lambda p: os.path.isfile(p) and os.access(p, os.X_OK),
    )


def validate_local_config(
    config: TranscriptionConfig, probes: ValidationProbes | None = None
) -> LocalValidation:
    probes = probes or default_probes()

    if config.engine not in ALLOWED_ENGINES:
        return _invalid(f"Engine de transcrição local não suportado: {config.engine!r}.")
    if config.model not in ALLOWED_MODELS:
        return _invalid(f"Modelo local não suportado: {config.model!r}.")

    if config.engine == "faster-whisper":
        return _validate_faster_whisper(config, probes)
    return _validate_whisper_cpp(config, probes)


def _validate_faster_whisper(
    config: TranscriptionConfig, probes: ValidationProbes
) -> LocalValidation:
    if config.compute_type not in ALLOWED_COMPUTE_TYPES:
        return _invalid(
            f"compute_type não suportado para CPU: {config.compute_type!r}."
        )
    if not probes.module_available("faster_whisper"):
        return _invalid(
            "O pacote faster-whisper não está instalado nesta imagem "
            "(INSTALL_FASTER_WHISPER=true)."
        )
    return LocalValidation(
        valid=True, summary=f"faster-whisper {config.model} {config.compute_type}"
    )


def _validate_whisper_cpp(
    config: TranscriptionConfig, probes: ValidationProbes
) -> LocalValidation:
    if config.quantization not in ALLOWED_QUANTIZATIONS:
        return _invalid(
            f"Quantização whisper.cpp não suportada: {config.quantization!r}."
        )
    if not config.whisper_cpp_binary or not probes.is_executable(
        config.whisper_cpp_binary
    ):
        return _invalid(
            "Binário whisper.cpp ausente ou não executável (WHISPER_CPP_BINARY)."
        )
    # whisper.cpp cannot fetch a ggml model itself, so model_path is ALWAYS
    # required (auto_download only applies to faster-whisper / HuggingFace).
    if not config.model_path or not probes.path_exists(config.model_path):
        return _invalid(
            "Arquivo de modelo whisper.cpp ausente (LOCAL_TRANSCRIPTION_MODEL_PATH)."
        )
    return LocalValidation(
        valid=True, summary=f"whisper.cpp {config.model} {config.quantization}"
    )


def _invalid(reason: str) -> LocalValidation:
    return LocalValidation(valid=False, reason=reason)
