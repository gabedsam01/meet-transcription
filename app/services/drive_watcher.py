"""Polling Drive watcher: list a user's source folder and enqueue new media.

This generalizes :func:`app.services.job_service.create_next_pending_job` (which
creates a single job) so the auto-poll loop and the "Verificar agora" button can
create up to ``max_files`` jobs per user in one pass. Like the run-once path it
only lists Drive metadata and creates ``pending`` jobs — never downloads or
transcribes. The caller enqueues the returned ids onto the queue.

Drive Changes API (incremental pageToken sync) is a documented next step; this
MVP lists the folder each poll and relies on ``last_poll_at`` gating plus the
``(user_id, source_file_id)`` dedupe to keep the work bounded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.services.job_service import BLOCKING_STATUSES

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollResult:
    created: int
    skipped: int
    error_code: str | None = None
    error_message: str | None = None
    job_ids: tuple[int, ...] = field(default_factory=tuple)


def poll_user(
    repositories,
    build_drive_client: Callable,
    credentials_from_token: Callable,
    user_id: int,
    *,
    now: datetime,
    max_files: int,
    deepgram_required: bool = True,
    guardrails=None,
) -> PollResult:
    """List the user's source folder and create up to ``max_files`` pending jobs.

    Returns a :class:`PollResult`. A hard precondition/Drive failure sets a friendly
    ``error_code``/``error_message`` (never a traceback); a guardrail limit is a
    soft notice in ``error_message`` with ``error_code=None``.
    """
    settings = repositories.settings.get(user_id)
    if settings is None or not settings.source_drive_folder_id:
        return PollResult(0, 0, "NO_SETTINGS", "Configure a pasta de origem no Drive.")

    token = repositories.google_tokens.get(user_id)
    if token is None:
        return PollResult(0, 0, "NOT_CONNECTED", "Conecte sua conta Google.")

    if deepgram_required and not settings.deepgram_api_key:
        return PollResult(
            0, 0, "NO_DEEPGRAM_KEY", "Configure sua Deepgram API Key."
        )

    credentials = credentials_from_token(token)
    drive = build_drive_client(
        credentials, settings.source_drive_folder_id, settings.destination_drive_folder_id
    )
    try:
        files = drive.list_video_files()
    except Exception as exc:  # noqa: BLE001 - surface a friendly message, log detail.
        LOGGER.warning("Drive poll failed for user_id=%s: %s", user_id, exc)
        return PollResult(
            0, 0, "DRIVE_ERROR", "Não foi possível listar a pasta do Drive."
        )

    # Daily-budget room (None = unlimited). Computed once per poll.
    room = guardrails.daily_room(repositories, user_id, now) if guardrails else None

    created_ids: list[int] = []
    skipped = 0
    notice: str | None = None
    for file in files:
        if len(created_ids) >= max_files:
            break
        if room is not None and len(created_ids) >= room:
            notice = "Limite diário de jobs atingido."
            break
        existing = repositories.jobs.find_existing_job(user_id, file.id, BLOCKING_STATUSES)
        if existing is not None:
            skipped += 1
            continue
        if guardrails is not None:
            allowed, reason = guardrails.allow_file(file)
            if not allowed:
                skipped += 1
                notice = reason or notice
                continue
        job = repositories.jobs.create_job(
            user_id=user_id, source_file_id=file.id,
            source_file_name=file.name, now=now,
        )
        created_ids.append(job.id)
        LOGGER.info(
            "Auto-poll queued job_id=%s user_id=%s source_file_id=%s",
            job.id, user_id, file.id,
        )

    return PollResult(len(created_ids), skipped, None, notice, tuple(created_ids))
