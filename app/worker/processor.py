from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.core.models import Job
from app.processor import sanitize_filename
from app.transcription.config import TranscriptionConfig
from app.transcription.deepgram_provider import DeepgramProvider
from app.transcription.factory import build_local_provider, resolve_provider
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
            if not job.source_file_id:
                raise RuntimeError("Job has no source_file_id to download")

            # Pick the transcription provider per the local/Deepgram product rule.
            # A valid local engine needs no Deepgram key; otherwise a per-user key is
            # required and its absence raises a clear, Deepgram-mentioning error.
            provider = self._resolve_provider(settings)

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
            LOGGER.info("Job completed job_id=%s", job.id)
        except Exception as exc:  # noqa: BLE001 - a job must always reach a terminal state.
            LOGGER.exception("Job failed job_id=%s", job.id)
            repos.jobs.mark_failed(job.id, str(exc), self._now())
        finally:
            _cleanup_job_dir(job_dir)

    def _resolve_provider(self, settings):
        config = self.container.transcription_config or TranscriptionConfig.disabled()

        def build_deepgram_provider():
            client = self.container.build_deepgram_client(settings.deepgram_api_key)
            return DeepgramProvider(
                client,
                model=self.container.settings.deepgram_model,
                language=self.container.settings.deepgram_language,
            )

        provider, _status = resolve_provider(
            config,
            has_deepgram_key=bool(settings.deepgram_api_key),
            build_local_provider=self.container.build_local_provider or build_local_provider,
            build_deepgram_provider=build_deepgram_provider,
            probes=self.container.transcription_probes,
        )
        return provider


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
