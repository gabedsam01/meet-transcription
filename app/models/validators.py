"""Presence checks for local whisper models.

Side-effecting filesystem access goes through an injectable ``path_exists``
callable (defaulting to :func:`os.path.exists`) so tests need no real files.
"""

from __future__ import annotations

import os
from typing import Callable

from app.models.manifest import (
    faster_whisper_repo,
    whisper_cpp_filename,
)
from app.transcription.config import TranscriptionConfig

PathExists = Callable[[str], bool]


def expected_whisper_cpp_path(config: TranscriptionConfig) -> str:
    """Return where the whisper.cpp ggml model is expected on disk.

    ``config.model_path`` wins if set; otherwise ``model_dir/<ggml filename>``.
    """

    if config.model_path:
        return config.model_path
    filename = whisper_cpp_filename(config.model, config.quantization or None)
    return os.path.join(config.model_dir, filename)


def whisper_cpp_model_present(
    config: TranscriptionConfig, *, path_exists: PathExists = os.path.exists
) -> bool:
    """True when the whisper.cpp ggml model exists on disk."""

    return bool(path_exists(expected_whisper_cpp_path(config)))


def faster_whisper_model_present(
    config: TranscriptionConfig, *, path_exists: PathExists = os.path.exists
) -> bool:
    """Best-effort check for a downloaded faster-whisper HF snapshot.

    HuggingFace caches a repo ``Systran/faster-whisper-<model>`` into a directory
    named ``models--Systran--faster-whisper-<model>`` under the cache root. We
    treat the presence of that directory as "model available".
    """

    repo = faster_whisper_repo(config.model)
    snapshot_dirname = "models--" + repo.replace("/", "--")
    snapshot = os.path.join(config.model_dir, snapshot_dirname)
    return bool(path_exists(snapshot))


__all__ = [
    "expected_whisper_cpp_path",
    "whisper_cpp_model_present",
    "faster_whisper_model_present",
]
