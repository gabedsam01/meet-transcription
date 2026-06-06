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
    # Queue consumers + retry/backoff policy (redis-queue mode).
    queue_concurrency: int = 5
    job_max_attempts: int = 3
    job_retry_base_seconds: int = 60
    job_retry_max_seconds: int = 3600
    # Auto-poll loop.
    auto_poll_enabled: bool = False
    auto_poll_interval_seconds: int = 300
    auto_poll_max_users_per_tick: int = 50
    auto_poll_max_files_per_user: int = 5
    auto_poll_lock_ttl_seconds: int = 240
    # Cost guardrail global defaults (0 = unlimited; per-user settings override).
    max_file_size_mb: int = 0
    daily_jobs_limit: int = 0

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
            queue_concurrency=_positive_int(values, "TRANSCRIPTION_QUEUE_CONCURRENCY", 5),
            job_max_attempts=_positive_int(values, "JOB_MAX_ATTEMPTS", 3),
            job_retry_base_seconds=_positive_int(values, "JOB_RETRY_BASE_SECONDS", 60),
            job_retry_max_seconds=_positive_int(values, "JOB_RETRY_MAX_SECONDS", 3600),
            auto_poll_enabled=parse_bool(values.get("AUTO_POLL_ENABLED", "false")),
            auto_poll_interval_seconds=_positive_int(
                values, "AUTO_POLL_INTERVAL_SECONDS", 300
            ),
            auto_poll_max_users_per_tick=_positive_int(
                values, "AUTO_POLL_MAX_USERS_PER_TICK", 50
            ),
            auto_poll_max_files_per_user=_positive_int(
                values, "AUTO_POLL_MAX_FILES_PER_USER", 5
            ),
            auto_poll_lock_ttl_seconds=_positive_int(
                values, "AUTO_POLL_LOCK_TTL_SECONDS", 240
            ),
            max_file_size_mb=_non_negative_int(values, "MAX_FILE_SIZE_MB", 0),
            daily_jobs_limit=_non_negative_int(values, "DAILY_JOBS_LIMIT", 0),
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


def _non_negative_int(env: Mapping[str, str], key: str, default: int) -> int:
    """Like ``_positive_int`` but 0 is allowed (used for '0 = unlimited' limits)."""
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if number < 0:
        raise ValueError(f"{key} must not be negative")
    return number
