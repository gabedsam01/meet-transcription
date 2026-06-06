from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.transcription.provider_config import ModelSettings


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Job:
    id: int
    user_id: int
    status: str
    source_file_id: str | None = None
    source_file_name: str | None = None
    transcript_drive_file_id: str | None = None
    error_message: str | None = None
    last_error_code: str | None = None
    next_retry_at: datetime | None = None
    attempts: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    processed_at: datetime | None = None


@dataclass
class Settings:
    user_id: int
    source_drive_folder_id: str
    destination_drive_folder_id: str
    save_copy_to_drive: bool = False
    deepgram_api_key: str | None = None
    # Per-user provider selection + decrypted cloud credentials (Models tab). Both
    # optional/empty by default so the legacy Deepgram-only path is unchanged.
    model_settings: ModelSettings | None = None
    provider_credentials: dict[str, str] = field(default_factory=dict)


@dataclass
class GoogleToken:
    access_token: str
    token_uri: str
    client_id: str
    refresh_token: str | None = None
    client_secret: str | None = None
    scopes: str | None = None
    expiry: str | None = None


@dataclass
class AutomationSettings:
    """Per-user auto-poll configuration + status + guardrail overrides.

    NULL guardrail fields fall back to the global env defaults. ``last_*`` mirror
    the most recent poll attempt for the UI.
    """

    user_id: int
    auto_poll_enabled: bool = False
    poll_interval_seconds: int = 300
    max_files_per_poll: int = 5
    last_poll_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    daily_jobs_limit: int | None = None
    max_file_size_mb: int | None = None
    monthly_cloud_minutes_limit: int | None = None
    max_file_duration_minutes: int | None = None


@dataclass
class Transcript:
    id: int
    job_id: int
    user_id: int
    text: str
    json_payload: dict[str, Any] | None = None
    drive_file_id: str | None = None
    created_at: datetime | None = None
