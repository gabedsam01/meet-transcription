"""Local transcription model manager.

Download / validate / configure local whisper models (whisper.cpp ggml files and
faster-whisper HuggingFace snapshots). This is NOT the SQLAlchemy ORM
(``app.database.models``).

Public surface used by the worker / web wiring::

    from app.models import ensure_model, ModelStatus

All side-effecting work (HTTP downloads, model snapshots, filesystem checks) is
injectable, and heavy optional deps (huggingface_hub) are imported lazily, so
importing this package only needs ``requirements.txt``.
"""

from __future__ import annotations

from app.models.errors import (
    AutoDownloadDisabledError,
    ModelDownloadError,
    ModelManagerError,
    UnknownModelError,
)
from app.models.manager import ModelStatus, ensure_model
from app.models.manifest import (
    WHISPER_CPP_HF_REPO,
    ModelSpec,
    faster_whisper_repo,
    resolve_spec,
    whisper_cpp_download_url,
    whisper_cpp_filename,
)
from app.models.validators import (
    expected_whisper_cpp_path,
    faster_whisper_model_present,
    whisper_cpp_model_present,
)

__all__ = [
    # errors
    "ModelManagerError",
    "UnknownModelError",
    "ModelDownloadError",
    "AutoDownloadDisabledError",
    # manifest
    "WHISPER_CPP_HF_REPO",
    "ModelSpec",
    "whisper_cpp_filename",
    "whisper_cpp_download_url",
    "faster_whisper_repo",
    "resolve_spec",
    # validators
    "expected_whisper_cpp_path",
    "whisper_cpp_model_present",
    "faster_whisper_model_present",
    # manager
    "ModelStatus",
    "ensure_model",
]
