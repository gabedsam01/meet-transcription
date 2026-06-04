from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


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
class Transcript:
    id: int
    job_id: int
    user_id: int
    text: str
    json_payload: dict[str, Any] | None = None
    drive_file_id: str | None = None
    created_at: datetime | None = None
