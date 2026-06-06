from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.models import Job
from app.errors import (
    AppError,
    DriveFolderMissingError,
    GoogleTokenMissingError,
    classify_error,
)
from app.processor import sanitize_filename
from app.transcription.config import TranscriptionConfig
from app.transcription.deepgram_provider import DeepgramProvider
from app.transcription.factory import build_local_provider, resolve_provider
from app.transcription.provider_kind import classify_provider_kind
from app.worker.container import WorkerContainer

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ResolvedProvider:
    """A job's resolved transcription provider plus the context to run it.

    ``kind`` ('cloud'/'local') drives which concurrency slot the queue loop
    acquires before processing; it is derived from the resolved provider's
    identity, not merely from whether a local engine is configured.
    """

    provider: Any
    name: str
    kind: str
    status: Any
    settings: Any
    token: Any


class JobProcessor:
    def __init__(self, container: WorkerContainer, now=_utc_now) -> None:
        self.container = container
        self._now = now

    def resolve(self, job: Job) -> ResolvedProvider:
        """Load settings/token and resolve the provider + concurrency kind.

        Raises a terminal :class:`AppError` (missing settings/token, or no
        provider available) — the caller dead-letters those without retry.
        """
        repos = self.container.repositories
        settings = repos.settings.get(job.user_id)
        if settings is None:
            raise DriveFolderMissingError("User settings are required before transcription")
        token = repos.google_tokens.get(job.user_id)
        if token is None:
            raise GoogleTokenMissingError("Google token is required before transcription")
        provider, status = self._resolve_provider(settings)
        name = getattr(provider, "name", None)
        if not name:
            name = "local" if getattr(status, "local_valid", False) else "deepgram"
        return ResolvedProvider(
            provider=provider, name=name, kind=classify_provider_kind(name),
            status=status, settings=settings, token=token,
        )

    def process(self, job: Job, resolved: ResolvedProvider | None = None) -> None:
        repos = self.container.repositories
        job_dir = Path(self.container.settings.tmp_dir) / "jobs" / str(job.id)
        started = time.monotonic()
        try:
            if resolved is None:
                resolved = self.resolve(job)
            settings = resolved.settings
            if not job.source_file_id:
                raise DriveFolderMissingError("Job has no source_file_id to download")

            provider = resolved.provider
            label = resolved.name
            LOGGER.info(
                "Transcription started: job_id=%s user_id=%s provider=%s kind=%s",
                job.id, job.user_id, label, resolved.kind,
            )

            credentials = self.container.credentials_from_token(resolved.token)
            drive = self.container.build_drive_client(
                credentials,
                settings.source_drive_folder_id,
                settings.destination_drive_folder_id,
            )

            job_dir.mkdir(parents=True, exist_ok=True)
            safe_base = sanitize_filename(job.source_file_name or job.source_file_id)
            video_path = job_dir / f"{safe_base}.mp4"
            drive.download_by_id(job.source_file_id, video_path)

            result = provider.transcribe(
                video_path,
                original_name=job.source_file_name or "",
                file_id=job.source_file_id,
            )
            transcript_text = result.text

            transcript_drive_file_id = None
            if settings.save_copy_to_drive and settings.destination_drive_folder_id:
                transcript_filename = f"{safe_base}_Transcricao.txt"
                transcript_path = job_dir / transcript_filename
                transcript_path.write_text(transcript_text, encoding="utf-8")
                transcript_drive_file_id = drive.upload_text_file(
                    transcript_path, transcript_filename
                )

            repos.transcripts.create(
                job_id=job.id, user_id=job.user_id, text=transcript_text,
                json_payload=result.payload, drive_file_id=transcript_drive_file_id,
                now=self._now(),
            )
            repos.jobs.mark_completed(
                job.id, self._now(), transcript_drive_file_id=transcript_drive_file_id
            )
            LOGGER.info(
                "Transcription completed: job_id=%s provider=%s duration_seconds=%.1f",
                job.id, label, time.monotonic() - started,
            )
        except Exception as exc:  # noqa: BLE001 - a job must always reach a terminal state.
            self._handle_failure(job, exc)
        finally:
            _cleanup_job_dir(job_dir)

    def _handle_failure(self, job: Job, exc: Exception) -> None:
        """Retry transient failures with backoff; dead-letter terminal/exhausted ones.

        ``attempts`` was already incremented when the job was claimed, so the first
        failure has ``attempts == 1``. Friendly message for the UI; traceback to logs.
        """
        repos = self.container.repositories
        settings = self.container.settings
        code, retryable, retry_after = classify_error(exc)
        user_message = exc.user_message if isinstance(exc, AppError) else str(exc)
        LOGGER.exception("Transcription failed: job_id=%s code=%s", job.id, code)

        if retryable and job.attempts < settings.job_max_attempts:
            delay = _backoff(
                job.attempts, settings.job_retry_base_seconds,
                settings.job_retry_max_seconds, retry_after,
            )
            repos.jobs.schedule_retry(
                job.id, self._now(),
                next_retry_at=self._now() + timedelta(seconds=delay),
                error_code=code, error_message=user_message,
            )
            LOGGER.info(
                "Job scheduled for retry: job_id=%s attempt=%s delay_seconds=%s",
                job.id, job.attempts, delay,
            )
            return

        repos.jobs.mark_failed(job.id, user_message, self._now(), error_code=code)
        queue = self.container.queue
        if queue is not None:
            try:
                queue.mark_dead(job.id)
            except Exception:  # noqa: BLE001 - DLQ bookkeeping must not crash the loop.
                LOGGER.warning("Could not add job_id=%s to the dead-letter set", job.id)

    def _resolve_provider(self, settings):
        config = self.container.transcription_config or TranscriptionConfig.disabled()

        def build_deepgram_provider():
            client = self.container.build_deepgram_client(settings.deepgram_api_key)
            return DeepgramProvider(
                client,
                model=self.container.settings.deepgram_model,
                language=self.container.settings.deepgram_language,
            )

        return resolve_provider(
            config,
            has_deepgram_key=bool(settings.deepgram_api_key),
            build_local_provider=self.container.build_local_provider or build_local_provider,
            build_deepgram_provider=build_deepgram_provider,
            probes=self.container.transcription_probes,
        )


def _backoff(attempts: int, base: int, maximum: int, retry_after: int | None) -> int:
    """Exponential backoff in seconds, floored by a provider's Retry-After.

    ``attempts`` is the post-claim count (1 on first failure), so the first retry
    waits ``base`` seconds, then doubles, capped at ``maximum``.
    """
    delay = min(maximum, base * (2 ** max(0, attempts - 1)))
    if retry_after:
        delay = max(delay, int(retry_after))
    return delay


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
