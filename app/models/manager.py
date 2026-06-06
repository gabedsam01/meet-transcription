"""High-level local model manager: ensure a model is ready before transcription.

``ensure_model`` is the single entry point the worker calls. It never downloads
in tests because the fetcher / faster-whisper downloader / ``path_exists`` are all
injectable. Importing this module pulls in NO heavy dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from app.models.downloader import (
    FasterWhisperDownloader,
    Fetcher,
    download_faster_whisper_model,
    download_whisper_cpp_model,
)
from app.models.errors import UnknownModelError
from app.models.validators import (
    expected_whisper_cpp_path,
    faster_whisper_model_present,
    whisper_cpp_model_present,
)
from app.transcription.config import (
    ALLOWED_ENGINES,
    ALLOWED_MODELS,
    TranscriptionConfig,
)

PathExists = Callable[[str], bool]


@dataclass(frozen=True)
class ModelStatus:
    """Outcome of preparing a local model."""

    engine: str
    model: str
    ready: bool
    path: str | None
    message: str
    reason: str | None = None


def ensure_model(
    config: TranscriptionConfig,
    *,
    fetcher: Fetcher | None = None,
    fw_downloader: FasterWhisperDownloader | None = None,
    path_exists: PathExists = os.path.exists,
) -> ModelStatus:
    """Ensure the configured local model is present, downloading if allowed.

    - Disabled config -> a ready no-op (nothing to prepare).
    - Unknown engine or model -> :class:`UnknownModelError`.
    - whisper-cpp: ready if present; else download when ``auto_download``;
      else not ready (model absent, download disabled).
    - faster-whisper: ready if the HF snapshot is present; else eager download
      when ``auto_download``; else not ready (it will be fetched lazily at first
      transcription only if auto-download is on).
    """

    if not config.enabled:
        return ModelStatus(
            engine=config.engine,
            model=config.model,
            ready=True,
            path=None,
            message="Transcrição local desativada; nada a preparar.",
        )

    if config.engine not in ALLOWED_ENGINES:
        raise UnknownModelError(f"Unsupported local engine: {config.engine!r}")
    if config.model not in ALLOWED_MODELS:
        raise UnknownModelError(f"Unsupported local model: {config.model!r}")

    if config.engine == "whisper-cpp":
        return _ensure_whisper_cpp(config, fetcher=fetcher, path_exists=path_exists)
    return _ensure_faster_whisper(
        config, fw_downloader=fw_downloader, path_exists=path_exists
    )


def _ensure_whisper_cpp(
    config: TranscriptionConfig,
    *,
    fetcher: Fetcher | None,
    path_exists: PathExists,
) -> ModelStatus:
    expected = expected_whisper_cpp_path(config)

    if whisper_cpp_model_present(config, path_exists=path_exists):
        return ModelStatus(
            engine=config.engine,
            model=config.model,
            ready=True,
            path=expected,
            message=f"Modelo whisper.cpp pronto em {expected}.",
        )

    if config.auto_download:
        dest = download_whisper_cpp_model(
            config, fetcher=fetcher, path_exists=path_exists
        )
        return ModelStatus(
            engine=config.engine,
            model=config.model,
            ready=True,
            path=dest,
            message=f"Modelo whisper.cpp baixado em {dest}.",
        )

    return ModelStatus(
        engine=config.engine,
        model=config.model,
        ready=False,
        path=None,
        reason="modelo ausente",
        message=(
            "Modelo whisper.cpp ausente e download automático desativado "
            "(LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD)."
        ),
    )


def _ensure_faster_whisper(
    config: TranscriptionConfig,
    *,
    fw_downloader: FasterWhisperDownloader | None,
    path_exists: PathExists,
) -> ModelStatus:
    if faster_whisper_model_present(config, path_exists=path_exists):
        return ModelStatus(
            engine=config.engine,
            model=config.model,
            ready=True,
            path=config.model_dir,
            message=f"Modelo faster-whisper {config.model} disponível no cache.",
        )

    if config.auto_download:
        path = download_faster_whisper_model(config, downloader=fw_downloader)
        return ModelStatus(
            engine=config.engine,
            model=config.model,
            ready=True,
            path=path,
            message=f"Modelo faster-whisper {config.model} baixado em {path}.",
        )

    return ModelStatus(
        engine=config.engine,
        model=config.model,
        ready=False,
        path=None,
        reason="modelo ausente",
        message=(
            "Modelo faster-whisper ausente; será baixado na primeira transcrição "
            "apenas se o download automático estiver ativado "
            "(LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD)."
        ),
    )


__all__ = ["ModelStatus", "ensure_model"]
