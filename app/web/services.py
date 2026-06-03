from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import db
from app.deepgram_client import DeepgramClient
from app.drive_client import DriveClient
from app.google_auth import build_oauth_credentials  # noqa: F401
from app.processor import DriveFile, format_transcript, sanitize_filename
from app.web.config import WebSettings
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore


@dataclass(frozen=True)
class EnqueueResult:
    """Outcome of trying to start a run-once job from the request path.

    status is one of: "missing_settings", "not_connected", "already_running",
    "created". job carries the created pending job row when status == "created".
    """

    status: str
    job: Any | None = None


def enqueue_run_once_job(settings: WebSettings, user_id: int) -> EnqueueResult:
    """Validate and create a pending job. Fast and free of network I/O.

    This runs inside the HTTP request, so it must never download, transcribe,
    or upload. The actual work happens later in run_user_job_background.
    """
    user_settings = db.get_settings(settings.database_path, user_id)
    if (
        user_settings is None
        or not user_settings["source_drive_folder_id"]
        or not user_settings["destination_drive_folder_id"]
    ):
        logging.info("Run once rejected: missing settings user_id=%s", user_id)
        return EnqueueResult("missing_settings")

    token = TokenStore(
        settings.database_path, fernet_from_secret(settings.app_secret_key)
    ).get_for_user(user_id)
    if token is None:
        logging.info("Run once rejected: Google not connected user_id=%s", user_id)
        return EnqueueResult("not_connected")

    if db.get_active_job(settings.database_path, user_id) is not None:
        logging.info("Run once rejected: a job is already running user_id=%s", user_id)
        return EnqueueResult("already_running")

    job = db.create_job(settings.database_path, user_id=user_id, status="pending")
    logging.info("Run once job created job_id=%s user_id=%s", job["id"], user_id)
    return EnqueueResult("created", job)


def run_user_job_background(settings: WebSettings, job_id: int, user_id: int) -> None:
    """Run the heavy transcription work for one pending job.

    Designed to run after the HTTP response is sent (FastAPI BackgroundTasks).
    It always leaves the job in a terminal state: "completed" on success or
    "failed" with an error_message on any failure, never stuck in "processing".
    """
    logging.info("Background job started job_id=%s user_id=%s", job_id, user_id)
    try:
        user_settings = db.get_settings(settings.database_path, user_id)
        if user_settings is None:
            raise RuntimeError("User settings are required before running transcription")
        logging.info("Background job settings loaded job_id=%s", job_id)

        token = TokenStore(
            settings.database_path, fernet_from_secret(settings.app_secret_key)
        ).get_for_user(user_id)
        if token is None:
            raise RuntimeError("Google token is required before running transcription")
        logging.info("Background job token loaded job_id=%s", job_id)

        credentials = build_oauth_credentials(token)
        drive_client = DriveClient.from_credentials(
            credentials,
            user_settings["source_drive_folder_id"],
            user_settings["destination_drive_folder_id"],
        )
        deepgram_client = DeepgramClient(
            api_key=settings.deepgram_api_key,
            model="nova-2",
            language="pt-BR",
            smart_format=True,
            punctuate=True,
            diarize=True,
            utterances=True,
        )

        db.update_job(settings.database_path, job_id, status="processing", attempts=1)
        logging.info("Background job processing started job_id=%s", job_id)

        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        files = drive_client.list_video_files()
        if not files:
            logging.info("Background job found no video files job_id=%s", job_id)
            db.update_job(
                settings.database_path,
                job_id,
                status="failed",
                error_message="No video files found in the source folder.",
            )
            return

        file = files[0]
        db.update_job(
            settings.database_path,
            job_id,
            source_file_id=file.id,
            source_file_name=file.name,
        )
        transcript_drive_file_id = _process_file(
            drive_client, deepgram_client, settings.tmp_dir, file
        )
        logging.info(
            "Background job upload complete job_id=%s transcript_drive_file_id=%s",
            job_id,
            transcript_drive_file_id,
        )
        db.update_job(
            settings.database_path,
            job_id,
            status="completed",
            transcript_drive_file_id=transcript_drive_file_id,
            error_message=None,
        )
        logging.info("Background job completed job_id=%s", job_id)
    except Exception as exc:  # noqa: BLE001 - a failed job must record its error, never hang.
        logging.exception(
            "Background job failed job_id=%s user_id=%s: %s", job_id, user_id, exc
        )
        db.update_job(
            settings.database_path,
            job_id,
            status="failed",
            error_message=str(exc),
        )


def _process_file(
    drive_client: DriveClient,
    deepgram_client: DeepgramClient,
    tmp_dir: str | Path,
    file: DriveFile,
) -> str:
    tmp_path = Path(tmp_dir)
    safe_base = sanitize_filename(file.name)
    video_path = tmp_path / f"{file.id}_{safe_base}.mp4"
    transcript_filename = f"{safe_base}_Transcricao.txt"
    transcript_path = tmp_path / f"{file.id}_{transcript_filename}"

    try:
        drive_client.download_file(file, video_path)
        deepgram_response = deepgram_client.transcribe(video_path)
        transcript_text = format_transcript(deepgram_response, file.name, file.id)
        transcript_path.write_text(transcript_text, encoding="utf-8")
        return drive_client.upload_text_file(transcript_path, transcript_filename)
    finally:
        _unlink_if_exists(video_path)
        _unlink_if_exists(transcript_path)


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
