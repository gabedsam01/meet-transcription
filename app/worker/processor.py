from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from app.audio.config import AudioConfig
from app.audio.probe import probe_audio
from app.core.models import Job
from app.diarization.align import diarize_and_align
from app.diarization.config import DiarizationConfig
from app.diarization.errors import DiarizationUnavailableError
from app.errors import (
    AppError,
    DriveFolderMissingError,
    GoogleTokenMissingError,
    RecordingNotFoundError,
)
from app.processor import sanitize_filename
from app.recordings import (
    cleanup_recording,
    is_upload_source,
    recording_id_from_source,
    recordings_dir_from_env,
    resolve_recording_file,
)
from app.transcription.audio import extract_audio_to_wav
from app.transcription.config import TranscriptionConfig
from app.transcription.deepgram_provider import DeepgramProvider
from app.transcription.factory import build_local_provider, resolve_provider
from app.transcription.normalizer import render_local_text
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
        # A chrome-extension upload carries its media locally (shared recordings
        # dir), NOT on Google Drive, so it needs neither a Drive download nor a
        # Google token. Detected purely from the job's source_file_id sentinel.
        is_upload = is_upload_source(job.source_file_id)
        recording_id = (
            recording_id_from_source(job.source_file_id) if is_upload else None
        )
        try:
            settings = repos.settings.get(job.user_id)
            if settings is None and not is_upload:
                raise DriveFolderMissingError("User settings are required before transcription")
            token = repos.google_tokens.get(job.user_id)
            if token is None and not is_upload:
                raise GoogleTokenMissingError("Google token is required before transcription")
            if not is_upload and not job.source_file_id:
                raise DriveFolderMissingError("Job has no source_file_id to download")

            # Pick the transcription provider per the local/Deepgram product rule.
            # A valid local engine needs no Deepgram key; otherwise a per-user key is
            # required and its absence raises a clear, Deepgram-mentioning error.
            provider, status = self._resolve_provider(settings)
            label = status.summary if status.local_valid else "deepgram"
            LOGGER.info(
                "Transcription started: job_id=%s user_id=%s provider=%s source=%s",
                job.id, job.user_id, label, "upload" if is_upload else "drive",
            )

            job_dir.mkdir(parents=True, exist_ok=True)
            safe_base = sanitize_filename(
                job.source_file_name or job.source_file_id or "recording"
            )

            drive = None
            if is_upload:
                media_path = self._prepare_upload_media(recording_id, job_dir)
                original_name = job.source_file_name or "Gravação do Meet"
            else:
                credentials = self.container.credentials_from_token(token)
                drive = self.container.build_drive_client(
                    credentials,
                    settings.source_drive_folder_id,
                    settings.destination_drive_folder_id,
                )
                media_path = job_dir / f"{safe_base}.mp4"
                drive.download_by_id(job.source_file_id, media_path)
                original_name = job.source_file_name or ""

            # Optional audio preprocessing: fail fast (friendly) when the media has
            # no audio track. OFF by default — zero impact on the Drive+Deepgram path.
            self._check_audio(media_path)

            result = provider.transcribe(
                media_path,
                original_name=original_name,
                file_id=job.source_file_id,
            )
            transcript_text = result.text
            payload = result.payload

            # Optional local diarization (OFF by default). When it assigns speakers,
            # re-render the .txt so the download reflects them.
            transcript_text, payload = self._apply_diarization(
                media_path, job_dir, payload, transcript_text,
                original_name=original_name, file_id=job.source_file_id,
            )

            transcript_drive_file_id = None
            # Upload-job transcripts live in PostgreSQL (downloadable from the UI) and
            # are NOT copied to Drive — the recording never came from Drive.
            if (
                not is_upload
                and settings.save_copy_to_drive
                and settings.destination_drive_folder_id
                and drive is not None
            ):
                transcript_filename = f"{safe_base}_Transcricao.txt"
                transcript_path = job_dir / transcript_filename
                transcript_path.write_text(transcript_text, encoding="utf-8")
                transcript_drive_file_id = drive.upload_text_file(
                    transcript_path, transcript_filename
                )

            repos.transcripts.create(
                job_id=job.id, user_id=job.user_id, text=transcript_text,
                json_payload=payload, drive_file_id=transcript_drive_file_id,
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
            if recording_id is not None:
                cleanup_recording(self._recordings_dir(), recording_id)

    # -- helpers ------------------------------------------------------------

    def _recordings_dir(self) -> Path:
        return self.container.recordings_dir or recordings_dir_from_env()

    def _prepare_upload_media(self, recording_id: str, job_dir: Path) -> Path:
        """Copy the uploaded recording into the job workspace for transcription.

        Copying (not moving) keeps the original until the job reaches a terminal
        state, so a transient failure can be retried; the copy lives under job_dir
        and is removed with it.
        """
        source = resolve_recording_file(self._recordings_dir(), recording_id)
        if source is None:
            raise RecordingNotFoundError(
                f"Recording media not found for recording_id={recording_id}"
            )
        dest = job_dir / source.name
        shutil.copy2(source, dest)
        return dest

    def _check_audio(self, media_path: Path) -> None:
        config = self.container.audio_config or AudioConfig.disabled()
        if not config.enabled:
            return
        # probe_audio raises NoAudioTrackError (an AppError) when there is no audio
        # stream, which the outer handler turns into a friendly job failure.
        probe_audio(str(media_path), runner=self.container.audio_runner)

    def _apply_diarization(
        self, media_path: Path, job_dir: Path, payload: dict, transcript_text: str,
        *, original_name: str, file_id: str | None,
    ) -> tuple[str, dict]:
        config = self.container.diarization_config or DiarizationConfig.disabled()
        if not config.enabled:
            return transcript_text, payload

        from app.diarization.provider import get_diarization_status

        status = get_diarization_status(config, probes=self.container.diarization_probes)
        if not status.valid:
            if config.required:
                raise DiarizationUnavailableError(
                    status.reason or status.message, user_message=status.message
                )
            LOGGER.warning(
                "Diarization skipped (continuing without speakers): %s",
                status.reason or status.message,
            )
            return transcript_text, payload

        # Diarization needs a 16 kHz mono WAV; reuse the shared extractor.
        wav_path = job_dir / "diarization.wav"
        extract_audio_to_wav(
            str(media_path), str(wav_path), runner=self.container.audio_runner
        )
        provider = (
            self.container.build_diarization_provider(config)
            if self.container.build_diarization_provider
            else None
        )
        segments, _ = diarize_and_align(
            config, wav_path, payload.get("segments") or [],
            provider=provider, probes=self.container.diarization_probes,
        )
        payload = {**payload, "segments": segments}
        # Re-render the .txt so the speaker labels appear in the download.
        return render_local_text(payload, original_name, file_id or ""), payload

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
            has_deepgram_key=bool(settings and settings.deepgram_api_key),
            build_local_provider=self.container.build_local_provider or build_local_provider,
            build_deepgram_provider=build_deepgram_provider,
            probes=self.container.transcription_probes,
        )


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
