from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.deepgram_client import DeepgramClient
from app.drive_client import DriveClient, DRIVE_SCOPES
from app.processor import DriveFile, format_transcript, sanitize_filename
from app.web.config import WebSettings
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore


def run_once_for_user(settings: WebSettings, user_id: int) -> int:
    user_settings = db.get_settings(settings.database_path, user_id)
    if user_settings is None:
        raise RuntimeError("User settings are required before running transcription")

    token = TokenStore(
        settings.database_path, fernet_from_secret(settings.app_secret_key)
    ).get_for_user(user_id)
    if token is None:
        raise RuntimeError("Google token is required before running transcription")

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

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    processed_count = 0
    for file in drive_client.list_video_files():
        job = db.create_job(
            settings.database_path,
            user_id=user_id,
            status="processing",
            source_file_id=file.id,
            source_file_name=file.name,
        )
        db.update_job(settings.database_path, job["id"], attempts=1)
        try:
            transcript_drive_file_id = _process_file(
                drive_client, deepgram_client, settings.tmp_dir, file
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop run-once.
            db.update_job(
                settings.database_path,
                job["id"],
                status="failed",
                error_message=str(exc),
            )
            continue

        db.update_job(
            settings.database_path,
            job["id"],
            status="completed",
            transcript_drive_file_id=transcript_drive_file_id,
            error_message=None,
        )
        processed_count += 1

    return processed_count


def build_oauth_credentials(token: dict):
    from google.oauth2.credentials import Credentials

    scopes = token.get("scopes") or DRIVE_SCOPES
    if isinstance(scopes, str):
        scopes = scopes.split()
    info = dict(token)
    if "access_token" in info and "token" not in info:
        info["token"] = info["access_token"]
    if info.get("expiry"):
        info["expiry"] = _google_expiry(info["expiry"])
    return Credentials.from_authorized_user_info(info, scopes=scopes)


def _google_expiry(value: str) -> str:
    if value.endswith("Z"):
        return value.removesuffix("Z")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0).isoformat()


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
