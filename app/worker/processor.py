from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.audio.config import AudioConfig, get_provider_capabilities, ProviderCapabilities
from app.audio.errors import FfmpegError
from app.audio.chunking import chunk_audio
from app.audio.stitch import stitch_transcript_chunks
from app.audio.probe import probe_audio
from app.core.models import Job
from app.diarization.align import diarize_and_align
from app.diarization.config import DiarizationConfig
from app.diarization.errors import DiarizationUnavailableError
from app.errors import (
    AppError,
    DriveFolderMissingError,
    GoogleTokenMissingError,
    ProviderUnavailableError,
    RecordingNotFoundError,
    ProviderFileTooLargeError,
    classify_error,
    error_code,
    is_retryable,
)
from app.observability import log_event
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
from app.transcription.provider_kind import classify_provider_kind
from app.transcription.provider_models import GEMINI, OPENROUTER, GROQ, ASSEMBLYAI
from app.transcription.registry import resolve_cloud_provider
from app.webhooks import JOB_COMPLETED, JOB_FAILED
from app.worker.container import WorkerContainer

# A user's explicit cloud choice in the Models tab takes over the worker's
# provider selection; Deepgram stays on the legacy local-vs-Deepgram path so its
# diarize/utterances settings and the "no silent fallback" rule are unchanged.
_CLOUD_BRANCH = (OPENROUTER, GEMINI, GROQ, ASSEMBLYAI)

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ResolvedProvider:
    """A job's resolved transcription provider plus the context to run it.

    ``kind`` ('cloud'/'local') drives which concurrency slot the queue loop
    acquires before processing; it is derived from the resolved provider's
    *identity* (``name``), not merely from whether a local engine is configured —
    a user with a valid local engine who chose Deepgram is a CLOUD job. ``name`` is
    the bare provider id (deepgram/gemini/openrouter/faster-whisper/whisper-cpp);
    ``label`` is the richer display string (e.g. ``deepgram:nova-2``).
    """

    provider: Any
    name: str
    kind: str
    status: Any
    settings: Any
    token: Any
    label: str = ""
    model: str = ""


class JobProcessor:
    def __init__(self, container: WorkerContainer, now=_utc_now) -> None:
        self.container = container
        self._now = now

    def resolve(self, job: Job) -> ResolvedProvider:
        """Load settings/token and resolve the provider + concurrency kind.

        Raises a terminal :class:`AppError` (missing settings/token, or no
        provider available) — the caller dead-letters those without retry. A
        chrome-extension upload carries its media locally and may run without user
        settings/token, so those preconditions are skipped for uploads.
        """
        repos = self.container.repositories
        is_upload = is_upload_source(job.source_file_id)
        settings = repos.settings.get(job.user_id)
        if settings is None and not is_upload:
            raise DriveFolderMissingError("User settings are required before transcription")
        token = repos.google_tokens.get(job.user_id)
        if token is None and not is_upload:
            raise GoogleTokenMissingError("Google token is required before transcription")
        provider, label, name, model = self._resolve_provider(settings)
        return ResolvedProvider(
            provider=provider, name=name, kind=classify_provider_kind(name),
            status=None, settings=settings, token=token, label=label, model=model,
        )

    def process(self, job: Job, resolved: ResolvedProvider | None = None) -> None:
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
        completed = False
        label: str | None = None
        try:
            if resolved is None:
                resolved = self.resolve(job)
            settings = resolved.settings
            token = resolved.token
            if not is_upload and not job.source_file_id:
                raise DriveFolderMissingError("Job has no source_file_id to download")

            provider = resolved.provider
            label = resolved.label or resolved.name
            log_event(
                "transcription.started", logger=LOGGER,
                job_id=job.id, user_id=job.user_id, provider=label,
                kind=resolved.kind, source="upload" if is_upload else "drive",
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

            preprocessed = self._preprocess_media_if_needed(media_path, resolved, job_dir)
            if isinstance(preprocessed, Path):
                result = provider.transcribe(
                    preprocessed,
                    original_name=original_name,
                    file_id=job.source_file_id,
                )
                transcript_text = result.text
                payload = result.payload
            else:
                chunk_payloads = []
                for chunk_path, start_sec in preprocessed:
                    LOGGER.info("Transcribing chunk at offset %s seconds: %s", start_sec, chunk_path.name)
                    chunk_res = provider.transcribe(
                        chunk_path,
                        original_name=chunk_path.name,
                        file_id=job.source_file_id,
                    )
                    chunk_payloads.append({
                        "start_offset": start_sec,
                        "segments": chunk_res.payload.get("segments") or [],
                        "text": chunk_res.payload.get("text") or "",
                        "raw": chunk_res.payload.get("raw") or {},
                    })
                
                stitched = stitch_transcript_chunks(chunk_payloads)
                from app.transcription.normalizer import normalized_payload, render_transcript_text
                payload = normalized_payload(
                    provider=resolved.name,
                    engine=resolved.name,
                    model=resolved.model,
                    language=None,
                    text=stitched["text"],
                    segments=stitched["segments"],
                    raw={"chunks": [p["raw"] for p in chunk_payloads]}
                )
                transcript_text = render_transcript_text(payload, original_name or "", job.source_file_id or "")

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
            completed = True
        except Exception as exc:  # noqa: BLE001 - a job must always reach a terminal state.
            self._handle_failure(job, exc, started)
        finally:
            _cleanup_job_dir(job_dir)
            if recording_id is not None:
                cleanup_recording(self._recordings_dir(), recording_id)

        # Success bookkeeping runs AFTER the try/finally, so a logging or webhook
        # hiccup can never route a genuinely-completed job into the failure handler.
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

    def _handle_failure(self, job: Job, exc: Exception, started: float | None = None) -> None:
        """Retry transient failures with backoff; dead-letter terminal/exhausted ones.

        ``attempts`` was already incremented when the job was claimed, so the first
        failure has ``attempts == 1``. A scheduled retry is NOT a terminal outcome,
        so the structured failure log + webhook fire only once the job is actually
        dead-lettered. Friendly message for the UI; traceback to logs.
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

        # Terminal failure only: structured log + best-effort, secret-free webhook.
        # error_code()/is_retryable() use the UI/observability vocabulary
        # (AppError.code); the retry policy above uses classify_error (error_code).
        duration = round(time.monotonic() - started, 3) if started is not None else None
        log_event(
            "transcription.failed", logger=LOGGER, level=logging.ERROR,
            job_id=job.id, user_id=job.user_id,
            error_code=error_code(exc), retryable=is_retryable(exc),
            duration_seconds=duration,
        )
        # The webhook is external egress: send only the stable code and a curated,
        # secret-free message (AppError.user_message) — never raw str(exc), which
        # could carry third-party/internal error text.
        self._emit_webhook(
            JOB_FAILED, job_id=job.id, user_id=job.user_id, status="failed",
            source_file_id=job.source_file_id, source_file_name=job.source_file_name,
            error_code=error_code(exc),
            error_message=(
                exc.user_message if isinstance(exc, AppError)
                else "Falha no processamento da transcrição."
            ),
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
        """Return ``(provider, label, name, model)`` for the job's settings.

        Explicit OpenRouter/Gemini selection goes through the cloud resolver (with
        fallback); everything else (no selection, or Deepgram) keeps the legacy
        local-vs-Deepgram rule unchanged. ``name`` is the bare provider id used to
        classify the concurrency kind; ``label`` is the richer display string.
        """
        config = self.container.transcription_config or TranscriptionConfig.disabled()
        # An extension-upload job can run without user settings (settings is None);
        # the Models-tab cloud routing simply does not apply, so fall through to the
        # legacy local/Deepgram rule instead of dereferencing None.
        ms = settings.model_settings if settings is not None else None
        if ms is not None and ms.primary_provider in _CLOUD_BRANCH:
            resolved = resolve_cloud_provider(
                ms, self._cloud_credentials(settings), build=self._build_cloud_provider
            )
            return resolved.provider, resolved.label, resolved.provider_id, resolved.model
        # Honour an explicit per-user Deepgram model from the Models tab; the
        # local-vs-Deepgram product rule and its messages stay on the legacy path.
        deepgram_model = self.container.settings.deepgram_model
        if ms is not None and ms.primary_provider == "deepgram" and ms.primary_model:
            deepgram_model = ms.primary_model
        provider, status = self._resolve_legacy(settings, config, deepgram_model)
        if getattr(status, "local_valid", False):
            # A valid local engine: its config.engine name drives LOCAL concurrency.
            return provider, status.summary, config.engine, config.model
        return provider, f"deepgram:{deepgram_model}", "deepgram", deepgram_model

    def _resolve_legacy(self, settings, config, deepgram_model):
        def build_deepgram_provider():
            client = self.container.build_deepgram_client(
                settings.deepgram_api_key, deepgram_model
            )
            return DeepgramProvider(
                client,
                model=deepgram_model,
                language=self.container.settings.deepgram_language,
            )

        return resolve_provider(
            config,
            has_deepgram_key=bool(settings and settings.deepgram_api_key),
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

    def _preprocess_media_if_needed(
        self,
        media_path: Path,
        resolved: ResolvedProvider,
        job_dir: Path,
    ) -> Path | list[tuple[Path, float]]:
        config = self.container.audio_config or AudioConfig.disabled()
        capabilities = get_provider_capabilities(resolved.name, config)
        limit_bytes = capabilities.max_upload_mb * 1024 * 1024
        
        # Check initial file size
        size_bytes = media_path.stat().st_size if media_path.exists() else 0
        
        # If the file is small enough, no preprocessing for size is needed.
        if size_bytes <= limit_bytes:
            return media_path
            
        # If preprocessing is disabled, raise ProviderFileTooLargeError immediately
        if not config.enabled:
            if resolved.name.lower() == "groq":
                user_msg = "No free tier do Groq, cada upload deve ficar abaixo de 25 MB. O sistema tentará compactar e dividir automaticamente."
            else:
                user_msg = "O arquivo é grande demais para este provedor. Ative compressão/chunking ou escolha Deepgram/local."
            raise ProviderFileTooLargeError(
                f"File size {size_bytes} exceeds provider limit {limit_bytes}",
                provider=resolved.name,
                user_message=user_msg
            )
            
        # If we reach here, size > limit and preprocessing is enabled.
        # Let's try compression if enabled.
        runner = self.container.audio_runner
        
        # We start by compressing/extracting to FLAC or MP3.
        # 1. Try FLAC first:
        flac_path = job_dir / "preprocessed.flac"
        try:
            cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(media_path), "-vn", "-ar", "16000", "-ac", "1", "-map", "0:a:0", str(flac_path)]
            try:
                _run_ffmpeg(cmd, runner)
            except FfmpegError:
                cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(media_path), "-vn", "-ar", "16000", "-ac", "1", str(flac_path)]
                _run_ffmpeg(cmd, runner)
                
            flac_size = flac_path.stat().st_size if flac_path.exists() else 0
            if flac_size <= limit_bytes:
                return flac_path
        except Exception as exc:
            LOGGER.warning("FLAC compression/extraction failed: %s", exc)
            
        # 2. Try MP3 second (24k bitrate):
        mp3_path = job_dir / "preprocessed.mp3"
        try:
            cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(media_path), "-vn", "-ar", "16000", "-ac", "1", "-b:a", "24k", str(mp3_path)]
            _run_ffmpeg(cmd, runner)
            
            mp3_size = mp3_path.stat().st_size if mp3_path.exists() else 0
            if mp3_size <= limit_bytes:
                return mp3_path
        except Exception as exc:
            LOGGER.warning("MP3 compression failed: %s", exc)
            
        # 3. If still exceeds limit and chunking is supported:
        if capabilities.supports_chunking:
            # Extract to WAV first, since chunk_audio requires WAV for -c copy:
            wav_path = job_dir / "to_chunk.wav"
            cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(media_path), "-vn", "-ar", "16000", "-ac", "1", str(wav_path)]
            _run_ffmpeg(cmd, runner)
            
            chunk_dir = job_dir / "chunks"
            chunks = chunk_audio(
                wav_path,
                chunk_dir,
                max_duration_seconds=config.chunk_max_duration_seconds,
                overlap_seconds=config.chunk_overlap_seconds,
                runner=runner
            )
            
            # Now, for each chunk (WAV), compress it to preferred format (FLAC or MP3)
            processed_chunks = []
            for chunk in chunks:
                chunk_dest = chunk_dir / f"chunk_{chunk.index:04d}_compressed.{capabilities.preferred_format}"
                
                # Try preferred format (typically flac or mp3).
                if capabilities.preferred_format == "flac":
                    try:
                        cmd = ["ffmpeg", "-nostdin", "-y", "-i", chunk.path, "-vn", "-ar", "16000", "-ac", "1", str(chunk_dest)]
                        _run_ffmpeg(cmd, runner)
                        c_size = chunk_dest.stat().st_size if chunk_dest.exists() else 0
                        if c_size > limit_bytes:
                            chunk_dest = chunk_dir / f"chunk_{chunk.index:04d}_compressed.mp3"
                            cmd = ["ffmpeg", "-nostdin", "-y", "-i", chunk.path, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "24k", str(chunk_dest)]
                            _run_ffmpeg(cmd, runner)
                            c_size = chunk_dest.stat().st_size if chunk_dest.exists() else 0
                            if c_size > limit_bytes:
                                for br in ["16k", "8k"]:
                                    cmd = ["ffmpeg", "-nostdin", "-y", "-i", chunk.path, "-vn", "-ar", "16000", "-ac", "1", "-b:a", br, str(chunk_dest)]
                                    _run_ffmpeg(cmd, runner)
                                    if chunk_dest.stat().st_size <= limit_bytes:
                                        break
                    except Exception as exc:
                        raise FfmpegError(f"Failed to compress chunk {chunk.index} to FLAC/MP3: {exc}")
                else:
                    # Preferred is mp3:
                    try:
                        cmd = ["ffmpeg", "-nostdin", "-y", "-i", chunk.path, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "24k", str(chunk_dest)]
                        _run_ffmpeg(cmd, runner)
                        c_size = chunk_dest.stat().st_size if chunk_dest.exists() else 0
                        if c_size > limit_bytes:
                            for br in ["16k", "8k"]:
                                cmd = ["ffmpeg", "-nostdin", "-y", "-i", chunk.path, "-vn", "-ar", "16000", "-ac", "1", "-b:a", br, str(chunk_dest)]
                                _run_ffmpeg(cmd, runner)
                                if chunk_dest.stat().st_size <= limit_bytes:
                                    break
                    except Exception as exc:
                        raise FfmpegError(f"Failed to compress chunk {chunk.index} to MP3: {exc}")
                
                final_size = chunk_dest.stat().st_size if chunk_dest.exists() else 0
                if final_size > limit_bytes:
                    if resolved.name.lower() == "groq":
                        user_msg = "No free tier do Groq, cada upload deve ficar abaixo de 25 MB. O sistema tentará compactar e dividir automaticamente."
                    else:
                        user_msg = "O arquivo é grande demais para este provedor. Ative compressão/chunking ou escolha Deepgram/local."
                    raise ProviderFileTooLargeError(
                        f"Chunk size {final_size} exceeds provider limit {limit_bytes}",
                        provider=resolved.name,
                        user_message=user_msg
                    )
                processed_chunks.append((chunk_dest, chunk.start_seconds))
            return processed_chunks
            
        if resolved.name.lower() == "groq":
            user_msg = "No free tier do Groq, cada upload deve ficar abaixo de 25 MB. O sistema tentará compactar e dividir automaticamente."
        else:
            user_msg = "O arquivo é grande demais para este provedor. Ative compressão/chunking ou escolha Deepgram/local."
        raise ProviderFileTooLargeError(
            f"File size exceeds limit after compression attempts",
            provider=resolved.name,
            user_message=user_msg
        )


def _run_ffmpeg(cmd: list[str], runner=None) -> None:
    runner = runner or _default_runner
    res = runner(cmd)
    if getattr(res, "returncode", 0) != 0:
        stderr = (getattr(res, "stderr", "") or "")[:500]
        raise FfmpegError(f"ffmpeg failed: {stderr}")


def _default_runner(cmd: list[str]):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True)


def _backoff(attempts: int, base: int, maximum: int, retry_after: int | None) -> int:
    """Exponential backoff in seconds, floored by a provider's Retry-After.

    ``attempts`` is the post-claim count (1 on first failure), so the first retry
    waits ``base`` seconds, then doubles, capped at ``maximum``.
    """
    delay = min(maximum, base * (2 ** max(0, attempts - 1)))
    if retry_after:
        # Honor Retry-After as a floor, but never exceed the configured maximum:
        # a provider returning an absurd Retry-After must not park a job for days.
        delay = max(delay, min(int(retry_after), maximum))
    return delay


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        LOGGER.warning("Could not remove job workspace %s: %s", job_dir, exc)
