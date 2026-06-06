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
    ProviderUnavailableError,
)
from app.processor import sanitize_filename
from app.transcription.config import TranscriptionConfig
from app.transcription.deepgram_provider import DeepgramProvider
from app.transcription.factory import build_local_provider, resolve_provider
from app.transcription.provider_models import OPENROUTER, GEMINI
from app.transcription.registry import resolve_cloud_provider
from app.worker.container import WorkerContainer

# A user's explicit cloud choice in the Models tab takes over the worker's
# provider selection; Deepgram stays on the legacy local-vs-Deepgram path so its
# diarize/utterances settings and the "no silent fallback" rule are unchanged.
_CLOUD_BRANCH = (OPENROUTER, GEMINI)

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
        try:
            settings = repos.settings.get(job.user_id)
            if settings is None:
                raise DriveFolderMissingError("User settings are required before transcription")
            token = repos.google_tokens.get(job.user_id)
            if token is None:
                raise GoogleTokenMissingError("Google token is required before transcription")
            if not job.source_file_id:
                raise DriveFolderMissingError("Job has no source_file_id to download")

            # Pick the transcription provider. A user's explicit cloud selection
            # (Models tab) wins; otherwise the local/Deepgram product rule applies:
            # a valid local engine needs no key, else a per-user key is required and
            # its absence raises a clear, Deepgram-mentioning error.
            provider, label = self._resolve_provider(settings)
            LOGGER.info(
                "Transcription started: job_id=%s user_id=%s provider=%s",
                job.id, job.user_id, label,
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
            LOGGER.info(
                "Transcription completed: job_id=%s provider=%s duration_seconds=%.1f",
                job.id, label, time.monotonic() - started,
            )
        except Exception as exc:  # noqa: BLE001 - a job must always reach a terminal state.
            # Friendly message for the UI; full traceback only in the logs.
            user_message = exc.user_message if isinstance(exc, AppError) else str(exc)
            LOGGER.exception("Transcription failed: job_id=%s reason=%s", job.id, exc)
            repos.jobs.mark_failed(job.id, user_message, self._now())
        finally:
            _cleanup_job_dir(job_dir)

    def _resolve_provider(self, settings):
        """Return ``(provider, label)`` for the job's settings.

        Explicit OpenRouter/Gemini selection goes through the cloud resolver (with
        fallback); everything else (no selection, or Deepgram) keeps the legacy
        local-vs-Deepgram rule unchanged.
        """
        config = self.container.transcription_config or TranscriptionConfig.disabled()
        ms = settings.model_settings
        if ms is not None and ms.primary_provider in _CLOUD_BRANCH:
            resolved = resolve_cloud_provider(
                ms, self._cloud_credentials(settings), build=self._build_cloud_provider
            )
            return resolved.provider, resolved.label
        provider, status = self._resolve_legacy(settings, config)
        return provider, (status.summary if status.local_valid else "deepgram")

    def _resolve_legacy(self, settings, config):
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

    def _cloud_credentials(self, settings) -> dict:
        creds = dict(settings.provider_credentials or {})
        # The legacy single Deepgram key also seeds the credentials map so a
        # Deepgram fallback works without a provider_credentials row.
        if settings.deepgram_api_key and not creds.get("deepgram"):
            creds["deepgram"] = settings.deepgram_api_key
        return creds

    def _build_cloud_provider(self, provider_id, model, api_key):
        if provider_id == "deepgram":
            client = self.container.build_deepgram_client(api_key)
            return DeepgramProvider(
                client, model=model, language=self.container.settings.deepgram_language
            )
        builder = self.container.build_cloud_provider
        if builder is None:
            raise ProviderUnavailableError(
                f"No builder configured for cloud provider {provider_id!r}",
                provider=provider_id,
            )
        return builder(provider_id, api_key=api_key, model=model)


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
