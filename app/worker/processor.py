from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from app.core.models import Job
from app.errors import (
    AppError,
    DriveFolderMissingError,
    GoogleTokenMissingError,
    error_code,
    is_retryable,
)
from app.observability import log_event
from app.processor import sanitize_filename
from app.transcription.config import TranscriptionConfig
from app.transcription.deepgram_provider import DeepgramProvider
from app.transcription.factory import build_local_provider, resolve_provider
from app.webhooks import JOB_COMPLETED, JOB_FAILED
from app.worker.container import WorkerContainer

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobProcessor:
    def __init__(self, container: WorkerContainer, now=_utc_now) -> None:
        self.container = container
        self._now = now

    def process(self, job: Job) -> None:
        repos = self.container.repositories
        job_dir = Path(self.container.settings.tmp_dir) / "jobs" / str(job.id)
        started = time.monotonic()
        completed = False
        try:
            settings = repos.settings.get(job.user_id)
            if settings is None:
                raise DriveFolderMissingError("User settings are required before transcription")
            token = repos.google_tokens.get(job.user_id)
            if token is None:
                raise GoogleTokenMissingError("Google token is required before transcription")
            if not job.source_file_id:
                raise DriveFolderMissingError("Job has no source_file_id to download")

            # Pick the transcription provider per the local/Deepgram product rule.
            # A valid local engine needs no Deepgram key; otherwise a per-user key is
            # required and its absence raises a clear, Deepgram-mentioning error.
            provider, status = self._resolve_provider(settings)
            label = status.summary if status.local_valid else "deepgram"
            log_event(
                "transcription.started", logger=LOGGER,
                job_id=job.id, user_id=job.user_id, provider=label,
            )

            credentials = self.container.credentials_from_token(token)
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
            completed = True
        except Exception as exc:  # noqa: BLE001 - a job must always reach a terminal state.
            # Friendly message for the UI; full traceback only in the logs.
            user_message = exc.user_message if isinstance(exc, AppError) else str(exc)
            LOGGER.exception("Transcription failed: job_id=%s reason=%s", job.id, exc)
            log_event(
                "transcription.failed", logger=LOGGER, level=logging.ERROR,
                job_id=job.id, user_id=job.user_id,
                error_code=error_code(exc), retryable=is_retryable(exc),
                duration_seconds=round(time.monotonic() - started, 3),
            )
            repos.jobs.mark_failed(job.id, user_message, self._now())
            # The webhook is external egress: send only the stable error_code and a
            # curated, secret-free message (AppError.user_message) — never raw str(exc),
            # which could carry third-party/internal error text.
            self._emit_webhook(
                JOB_FAILED, job_id=job.id, user_id=job.user_id, status="failed",
                source_file_id=job.source_file_id, source_file_name=job.source_file_name,
                error_code=error_code(exc),
                error_message=(
                    exc.user_message if isinstance(exc, AppError)
                    else "Falha no processamento da transcrição."
                ),
            )
        finally:
            _cleanup_job_dir(job_dir)

        # Success bookkeeping runs AFTER the try/finally, so a logging or webhook hiccup
        # can never route a genuinely-completed job into the failure handler above.
        if completed:
            log_event(
                "transcription.completed", logger=LOGGER,
                job_id=job.id, user_id=job.user_id, provider=label,
                duration_seconds=round(time.monotonic() - started, 3),
                error_code=None, retryable=False,
            )
            self._emit_webhook(
                JOB_COMPLETED, job_id=job.id, user_id=job.user_id, status="completed",
                source_file_id=job.source_file_id, source_file_name=job.source_file_name,
                error_code=None, error_message=None,  # same payload shape as job.failed
            )

    def _emit_webhook(self, event: str, **data) -> None:
        """Fire an outbound webhook best-effort. Never affects the job outcome."""
        notifier = self.container.webhook_notifier
        if notifier is None:
            return
        try:
            notifier.notify(event, data)
        except Exception:  # noqa: BLE001 - a webhook must never fail a job.
            LOGGER.warning("Webhook emission raised for %s; job is unaffected", event)

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


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
