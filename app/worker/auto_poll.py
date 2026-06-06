"""Auto-poll loop: a worker thread that periodically scans each enabled user's
Drive folder and enqueues new media.

Runs inside the worker process (no sixth container). One tick:

1. Acquire ``lock:auto_poll`` so only one poller runs across processes/threads.
2. Sweep due retry jobs back onto the queue (bounded cadence, even under load).
3. For each enabled, due user (capped per tick): poll the folder, enqueue new
   jobs, and record the outcome on ``user_automation_settings``.

Transient failures are logged and swallowed — the loop thread never dies.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.queue import requeue_pending_jobs
from app.services.drive_watcher import poll_user
from app.services.guardrails import resolve_guardrails
from app.transcription.config import TranscriptionConfig
from app.transcription.provider import get_transcription_provider_status
from app.worker.container import WorkerContainer

LOGGER = logging.getLogger(__name__)

_AUTO_POLL_LOCK = "lock:auto_poll"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_auto_poll_loop(
    container: WorkerContainer, stop_event: threading.Event, now=_utc_now
) -> None:
    """Run :func:`auto_poll_tick` every ``AUTO_POLL_INTERVAL_SECONDS`` until stopped."""
    interval = container.settings.auto_poll_interval_seconds
    LOGGER.info("Auto-poll loop started (interval=%ss)", interval)
    while not stop_event.is_set():
        try:
            auto_poll_tick(container, now)
        except Exception:  # noqa: BLE001 - a tick error must never kill the thread.
            LOGGER.exception("Auto-poll tick failed")
        stop_event.wait(interval)


def auto_poll_tick(container: WorkerContainer, now=_utc_now) -> int:
    """Run one poll pass. Returns the number of jobs created across users."""
    queue = container.queue
    repos = container.repositories
    settings = container.settings
    if queue is None or repos.automation is None:
        return 0

    token = queue.acquire_named_lock(_AUTO_POLL_LOCK, settings.auto_poll_lock_ttl_seconds)
    if token is None:
        LOGGER.debug("Another poller holds %s; skipping tick", _AUTO_POLL_LOCK)
        return 0

    created_total = 0
    try:
        # Re-enqueue due retry jobs on a bounded cadence (independent of queue load).
        requeue_pending_jobs(repos, queue, now())
        due = repos.automation.list_due_for_poll(
            now(), settings.auto_poll_max_users_per_tick
        )
        deepgram_required = _deepgram_required(container)
        for auto in due:
            created_total += _poll_one_user(container, auto, deepgram_required, now)
    finally:
        queue.release_named_lock(_AUTO_POLL_LOCK, token)
    return created_total


def _poll_one_user(container, auto, deepgram_required: bool, now) -> int:
    repos = container.repositories
    queue = container.queue
    settings = container.settings
    user_id = auto.user_id
    try:
        guardrails = resolve_guardrails(
            auto,
            default_max_file_size_mb=settings.max_file_size_mb or None,
            default_daily_jobs_limit=settings.daily_jobs_limit or None,
        )
        max_files = auto.max_files_per_poll or settings.auto_poll_max_files_per_user
        result = poll_user(
            repos,
            container.build_drive_client,
            container.credentials_from_token,
            user_id,
            now=now(),
            max_files=max_files,
            deepgram_required=deepgram_required,
            guardrails=guardrails,
        )
        for job_id in result.job_ids:
            try:
                queue.enqueue(job_id)
            except Exception:  # noqa: BLE001 - Postgres still has it pending; reconciler heals.
                LOGGER.warning("Could not enqueue job_id=%s after auto-poll", job_id)
        if result.error_code:
            repos.automation.mark_poll_result(
                user_id, now(), success=False,
                error_code=result.error_code, error_message=result.error_message,
            )
        else:
            repos.automation.mark_poll_result(user_id, now(), success=True)
        return result.created
    except Exception:  # noqa: BLE001 - one user's failure must not stop the others.
        LOGGER.exception("Auto-poll failed for user_id=%s", user_id)
        repos.automation.mark_poll_result(
            user_id, now(), success=False, error_code="POLL_ERROR",
            error_message="Falha ao verificar a pasta do Drive.",
        )
        return 0


def _deepgram_required(container) -> bool:
    """Whether a per-user Deepgram key is required (no valid local engine)."""
    config = container.transcription_config or TranscriptionConfig.disabled()
    return get_transcription_provider_status(
        config, probes=container.transcription_probes
    ).deepgram_required
