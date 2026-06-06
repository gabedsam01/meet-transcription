from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.core.models import Job
from app.core.ports import Repositories

LOGGER = logging.getLogger(__name__)

# A video is unavailable for a new job if it is already queued, running, or done.
BLOCKING_STATUSES = ("pending", "processing", "completed")


@dataclass(frozen=True)
class JobCreationResult:
    status: str  # created | no_settings | not_connected | no_provider_key | no_new_videos
    job: Job | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _has_provider_credential(settings) -> bool:
    """True when the user has any usable cloud provider key (legacy or new)."""
    if getattr(settings, "deepgram_api_key", None):
        return True
    creds = getattr(settings, "provider_credentials", None) or {}
    return any(bool(v) for v in creds.values())


def create_next_pending_job(
    repositories: Repositories,
    build_drive_client: Callable,
    credentials_from_token: Callable,
    user_id: int,
    now: Callable[[], datetime] = _utc_now,
    deepgram_required: bool = True,
) -> JobCreationResult:
    settings = repositories.settings.get(user_id)
    if settings is None or not settings.source_drive_folder_id:
        return JobCreationResult("no_settings")

    token = repositories.google_tokens.get(user_id)
    if token is None:
        return JobCreationResult("not_connected")

    # A per-user provider credential is mandatory before a job may be enqueued
    # *unless* a valid local engine is active (deepgram_required=False). Any cloud
    # provider key counts (Deepgram, OpenRouter, Gemini, Groq, AssemblyAI) —
    # enforcing it here keeps the UI from creating a job that is doomed to fail.
    if deepgram_required and not _has_provider_credential(settings):
        return JobCreationResult("no_provider_key")

    credentials = credentials_from_token(token)
    drive = build_drive_client(
        credentials, settings.source_drive_folder_id, settings.destination_drive_folder_id
    )

    for file in drive.list_video_files():
        existing = repositories.jobs.find_existing_job(user_id, file.id, BLOCKING_STATUSES)
        if existing is not None:
            continue
        job = repositories.jobs.create_job(
            user_id=user_id, source_file_id=file.id,
            source_file_name=file.name, now=now(),
        )
        LOGGER.info(
            "Job queued: job_id=%s user_id=%s source_file_id=%s",
            job.id, user_id, file.id,
        )
        return JobCreationResult("created", job)

    return JobCreationResult("no_new_videos")
