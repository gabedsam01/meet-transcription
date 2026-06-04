from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.config import parse_bool

# Models accepted for BOTH engines. Multilingual checkpoints only — `.en` models
# are intentionally excluded because we need pt-BR *and* English.
ALLOWED_MODELS = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
)
ALLOWED_ENGINES = ("faster-whisper", "whisper-cpp")
# CPU-appropriate compute types for faster-whisper (no GPU float16 by default).
ALLOWED_COMPUTE_TYPES = ("int8", "int8_float32", "float32")
# whisper.cpp quantizations supported by the MVP (q2/q3/q6 may exist in custom
# builds but are out of scope here).
ALLOWED_QUANTIZATIONS = ("q4_0", "q4_1", "q5_0", "q5_1", "q8_0")

DEFAULT_DOC_URL = (
    "https://github.com/gabedsam01/meet-transcription/blob/main/"
    "docs/architecture/local-transcription.md"
)


@dataclass(frozen=True)
class TranscriptionConfig:
    """Local transcription configuration loaded from the environment.

    ``from_env`` never raises on a *bad* value (an unknown engine/model just makes
    the config invalid later, requiring Deepgram) so it can never crash worker
    startup. Validity is decided by ``validate_local_config``, not here.
    """

    enabled: bool
    engine: str
    model: str
    language: str
    threads: int
    model_dir: str
    compute_type: str
    quantization: str
    model_path: str | None
    whisper_cpp_binary: str | None
    auto_download: bool
    doc_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TranscriptionConfig":
        values = env if env is not None else os.environ
        return cls(
            enabled=_bool(values, "LOCAL_TRANSCRIPTION_ENABLED", False),
            engine=_engine(values.get("LOCAL_TRANSCRIPTION_ENGINE", "faster-whisper")),
            model=values.get("LOCAL_TRANSCRIPTION_MODEL", "").strip() or "small",
            language=values.get("LOCAL_TRANSCRIPTION_LANGUAGE", "").strip() or "auto",
            threads=_int(values, "LOCAL_TRANSCRIPTION_THREADS", 4),
            model_dir=values.get("LOCAL_TRANSCRIPTION_MODEL_DIR", "").strip() or "/models",
            compute_type=values.get("LOCAL_TRANSCRIPTION_COMPUTE_TYPE", "").strip() or "int8",
            quantization=values.get("LOCAL_TRANSCRIPTION_QUANTIZATION", "").strip() or "q4_0",
            model_path=values.get("LOCAL_TRANSCRIPTION_MODEL_PATH", "").strip() or None,
            whisper_cpp_binary=values.get("WHISPER_CPP_BINARY", "").strip() or None,
            auto_download=_bool(values, "LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD", False),
            doc_url=values.get("LOCAL_TRANSCRIPTION_DOC_URL", "").strip() or DEFAULT_DOC_URL,
        )

    @classmethod
    def disabled(cls) -> "TranscriptionConfig":
        return cls.from_env({"LOCAL_TRANSCRIPTION_ENABLED": "false"})


def _engine(raw: str) -> str:
    return raw.strip().lower().replace("_", "-")


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
    return value if value > 0 else default
