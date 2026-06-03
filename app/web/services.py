from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.web.deepgram_key import DeepgramKeyStore
from app.web.repositories import RepositoryBundle


@dataclass(frozen=True)
class EnqueueResult:
    """status in: missing_settings | not_connected | no_deepgram_key | already_running | created."""

    status: str
    job: Any | None = None


def enqueue_run_once_job(
    repositories: RepositoryBundle, deepgram_store: DeepgramKeyStore, user_id: int
) -> EnqueueResult:
    """Validate preconditions and enqueue a pending job.

    This branch only enqueues. Real download/transcribe/persist is owned by the
    postgres-worker branch, which consumes pending jobs from the same repository.
    """
    drive_settings = repositories.drive_settings.get_for_user(user_id)
    if drive_settings is None or not drive_settings.source_drive_folder_id:
        return EnqueueResult("missing_settings")
    if repositories.google_tokens.get_for_user(user_id) is None:
        return EnqueueResult("not_connected")
    if not deepgram_store.has_key(user_id):
        return EnqueueResult("no_deepgram_key")
    if repositories.jobs.find_active_for_user(user_id) is not None:
        return EnqueueResult("already_running")
    job = repositories.jobs.create_job(user_id=user_id, status="pending")
    logging.info("Run once job enqueued job_id=%s user_id=%s", job.id, user_id)
    return EnqueueResult("created", job)
