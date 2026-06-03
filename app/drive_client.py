from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from app.processor import DriveFile


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
LOGGER = logging.getLogger(__name__)


class DriveClient:
    def __init__(self, settings):
        self.source_folder_id = settings.source_drive_folder_id
        self.destination_folder_id = settings.destination_drive_folder_id

        from googleapiclient.discovery import build

        LOGGER.info("Google auth mode: %s", settings.google_auth_mode)
        credentials = build_drive_credentials(settings)
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    @classmethod
    def from_credentials(
        cls, credentials, source_folder_id: str, destination_folder_id: str
    ) -> "DriveClient":
        from googleapiclient.discovery import build

        client = cls.__new__(cls)
        client.source_folder_id = source_folder_id
        client.destination_folder_id = destination_folder_id
        client.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return client

    def list_video_files(self) -> list[DriveFile]:
        files: list[DriveFile] = []
        page_token = None
        query = f"'{self.source_folder_id}' in parents and trashed = false"

        while True:
            response = (
                self.service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields=(
                        "nextPageToken, files(id,name,mimeType,size,createdTime,"
                        "modifiedTime,trashed)"
                    ),
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for item in response.get("files", []):
                if is_ready_video_file(item):
                    files.append(to_drive_file(item))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return sort_drive_files(files)

    def download_file(self, file: DriveFile, destination: str | Path) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(fileId=file.id, supportsAllDrives=True)

        with destination_path.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

    def upload_text_file(self, source_path: str | Path, filename: str) -> str:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(source_path), mimetype="text/plain", resumable=False)
        metadata = {
            "name": filename,
            "parents": [self.destination_folder_id],
            "mimeType": "text/plain",
        }
        created = (
            self.service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return created["id"]


def is_ready_video_file(item: dict[str, Any]) -> bool:
    if item.get("trashed") is True:
        return False

    name = str(item.get("name") or "")
    mime_type = str(item.get("mimeType") or "")
    is_mp4 = mime_type == "video/mp4" or name.lower().endswith(".mp4")
    if not is_mp4:
        return False

    size = item.get("size")
    if size is not None:
        try:
            if int(size) <= 0:
                return False
        except (TypeError, ValueError):
            return False

    return True


def to_drive_file(item: dict[str, Any]) -> DriveFile:
    size = item.get("size")
    return DriveFile(
        id=str(item["id"]),
        name=str(item["name"]),
        mime_type=str(item.get("mimeType") or ""),
        size=int(size) if size is not None else None,
        created_time=item.get("createdTime"),
        modified_time=item.get("modifiedTime"),
    )


def sort_drive_files(files: list[DriveFile]) -> list[DriveFile]:
    return sorted(files, key=lambda file: file.modified_time or file.created_time or "")


def build_drive_credentials(
    settings,
    service_account_credentials_cls=None,
    oauth_credentials_cls=None,
    request_factory=None,
):
    if settings.google_auth_mode == "service_account":
        if service_account_credentials_cls is None:
            from google.oauth2 import service_account

            service_account_credentials_cls = service_account.Credentials
        return service_account_credentials_cls.from_service_account_file(
            str(settings.google_service_account_file), scopes=DRIVE_SCOPES
        )

    if settings.google_auth_mode == "oauth":
        client_secrets_file = getattr(settings, "google_oauth_client_secrets_file", None)
        if client_secrets_file and not Path(client_secrets_file).exists():
            raise FileNotFoundError(
                f"OAuth client secrets file not found: {client_secrets_file}"
            )
        if oauth_credentials_cls is None:
            from google.oauth2.credentials import Credentials

            oauth_credentials_cls = Credentials
        credentials = oauth_credentials_cls.from_authorized_user_file(
            str(settings.google_oauth_token_file), DRIVE_SCOPES
        )
        if credentials.expired and credentials.refresh_token:
            if request_factory is None:
                from google.auth.transport.requests import Request

                request_factory = Request
            credentials.refresh(request_factory())
            _save_refreshed_token(settings.google_oauth_token_file, credentials)
        return credentials

    raise ValueError("GOOGLE_AUTH_MODE must be 'service_account' or 'oauth'")


def _save_refreshed_token(token_file: Path, credentials) -> None:
    try:
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("Could not save refreshed OAuth token to %s: %s", token_file, exc)
