"""Manifest of where local whisper models live and how to name them.

Pure, side-effect-free helpers: given a model/quantization (validated against the
allowed-lists in :mod:`app.transcription.config`), compute the on-disk filename,
the HuggingFace download URL and the faster-whisper repo id. ``resolve_spec``
turns a :class:`~app.transcription.config.TranscriptionConfig` into a
:class:`ModelSpec` describing the engine's model artifact.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.models.errors import UnknownModelError
from app.transcription.config import (
    ALLOWED_MODELS,
    ALLOWED_QUANTIZATIONS,
    TranscriptionConfig,
)

# Official ggml whisper.cpp checkpoints are published under this HF repo.
WHISPER_CPP_HF_REPO = "ggerganov/whisper.cpp"


def _check_model(model: str) -> None:
    if model not in ALLOWED_MODELS:
        raise UnknownModelError(f"Unsupported local model: {model!r}")


def _normalized_quantization(quantization: str | None) -> str | None:
    """Return a validated quantization, or ``None`` for an unquantized model.

    Empty string is treated as "no quantization" (the full-precision ggml file).
    """

    if not quantization:
        return None
    if quantization not in ALLOWED_QUANTIZATIONS:
        raise UnknownModelError(f"Unsupported whisper.cpp quantization: {quantization!r}")
    return quantization


def whisper_cpp_filename(model: str, quantization: str | None) -> str:
    """Return the ggml filename, e.g. ``ggml-small-q4_0.bin`` or ``ggml-small.bin``."""

    _check_model(model)
    quant = _normalized_quantization(quantization)
    if quant is None:
        return f"ggml-{model}.bin"
    return f"ggml-{model}-{quant}.bin"


def whisper_cpp_download_url(model: str, quantization: str | None) -> str:
    """Return the public HuggingFace ``resolve`` URL for the ggml model."""

    filename = whisper_cpp_filename(model, quantization)
    return f"https://huggingface.co/{WHISPER_CPP_HF_REPO}/resolve/main/{filename}"


def faster_whisper_repo(model: str) -> str:
    """Return the HuggingFace repo id for a faster-whisper checkpoint."""

    _check_model(model)
    return f"Systran/faster-whisper-{model}"


@dataclass(frozen=True)
class ModelSpec:
    """Describes the model artifact for a configured engine."""

    engine: str
    model: str
    quantization: str | None
    filename: str | None
    download_url: str | None
    repo: str | None


def resolve_spec(config: TranscriptionConfig) -> ModelSpec:
    """Build a :class:`ModelSpec` from a transcription config.

    For ``whisper-cpp`` the filename/download_url come from the manifest and the
    on-disk target is ``config.model_path`` or ``model_dir/<filename>``. For
    ``faster-whisper`` only the repo id is set (artifacts are a HF snapshot dir).
    """

    _check_model(config.model)

    if config.engine == "faster-whisper":
        return ModelSpec(
            engine=config.engine,
            model=config.model,
            quantization=None,
            filename=None,
            download_url=None,
            repo=faster_whisper_repo(config.model),
        )

    # Default / whisper-cpp branch.
    quant = config.quantization or None
    filename = whisper_cpp_filename(config.model, quant)
    return ModelSpec(
        engine=config.engine,
        model=config.model,
        quantization=_normalized_quantization(quant),
        filename=filename,
        download_url=whisper_cpp_download_url(config.model, quant),
        repo=None,
    )


def whisper_cpp_target_path(config: TranscriptionConfig) -> str:
    """Resolve the on-disk path for the whisper.cpp ggml model."""

    if config.model_path:
        return config.model_path
    filename = whisper_cpp_filename(config.model, config.quantization or None)
    return os.path.join(config.model_dir, filename)


__all__ = [
    "WHISPER_CPP_HF_REPO",
    "ModelSpec",
    "whisper_cpp_filename",
    "whisper_cpp_download_url",
    "faster_whisper_repo",
    "resolve_spec",
    "whisper_cpp_target_path",
]
