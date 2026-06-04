from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import parse_bool


@dataclass(frozen=True)
class WorkerSettings:
    repository_backend: str
    poll_interval_seconds: int
    concurrency: int
    stale_job_timeout_minutes: int
    tmp_dir: Path
    deepgram_model: str
    deepgram_language: str
    deepgram_smart_format: bool
    deepgram_punctuate: bool
    deepgram_diarize: bool
    deepgram_utterances: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WorkerSettings":
        values = env if env is not None else os.environ
        backend = values.get("WORKER_REPOSITORY_BACKEND", "postgres").strip().lower()
        return cls(
            repository_backend=backend or "postgres",
            poll_interval_seconds=_positive_int(values, "WORKER_POLL_INTERVAL_SECONDS", 10),
            concurrency=_positive_int(values, "WORKER_CONCURRENCY", 1),
            stale_job_timeout_minutes=_positive_int(values, "STALE_JOB_TIMEOUT_MINUTES", 60),
            tmp_dir=Path(values.get("TMP_DIR", "").strip() or "/app/tmp"),
            deepgram_model=values.get("DEEPGRAM_MODEL", "nova-3").strip() or "nova-3",
            deepgram_language=values.get("DEEPGRAM_LANGUAGE", "pt-BR").strip() or "pt-BR",
            deepgram_smart_format=parse_bool(values.get("DEEPGRAM_SMART_FORMAT", "true")),
            deepgram_punctuate=parse_bool(values.get("DEEPGRAM_PUNCTUATE", "true")),
            deepgram_diarize=parse_bool(values.get("DEEPGRAM_DIARIZE", "true")),
            deepgram_utterances=parse_bool(values.get("DEEPGRAM_UTTERANCES", "true")),
        )


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return number
