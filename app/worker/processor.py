from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.core.models import Job
from app.processor import format_transcript, sanitize_filename
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
        try:
            settings = repos.settings.get(job.user_id)
            if settings is None:
                raise RuntimeError("User settings are required before transcription")
            token = repos.google_tokens.get(job.user_id)
            if token is None:
                raise RuntimeError("Google token is required before transcription")
            if not settings.deepgram_api_key:
                raise RuntimeError("A per-user Deepgram API key is required")
            if not job.source_file_id:
                raise RuntimeError("Job has no source_file_id to download")

            credentials = self.container.credentials_from_token(token)
            drive = self.container.build_drive_client(
                credentials,
                settings.source_drive_folder_id,
                settings.destination_drive_folder_id,
            )
            deepgram = self.container.build_deepgram_client(settings.deepgram_api_key)

            job_dir.mkdir(parents=True, exist_ok=True)
            safe_base = sanitize_filename(job.source_file_name or job.source_file_id)
            video_path = job_dir / f"{safe_base}.mp4"
            drive.download_by_id(job.source_file_id, video_path)

            deepgram_response = deepgram.transcribe(video_path)
            transcript_text = format_transcript(
                deepgram_response, job.source_file_name or "", job.source_file_id
            )

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
                json_payload=deepgram_response, drive_file_id=transcript_drive_file_id,
                now=self._now(),
            )
            repos.jobs.mark_completed(
                job.id, self._now(), transcript_drive_file_id=transcript_drive_file_id
            )
            LOGGER.info("Job completed job_id=%s", job.id)
        except Exception as exc:  # noqa: BLE001 - a job must always reach a terminal state.
            LOGGER.exception("Job failed job_id=%s", job.id)
            repos.jobs.mark_failed(job.id, str(exc), self._now())
        finally:
            _cleanup_job_dir(job_dir)


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
