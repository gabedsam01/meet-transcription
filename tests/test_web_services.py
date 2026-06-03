from __future__ import annotations

import pytest

from app import db
from app.processor import DriveFile
from app.web.config import WebSettings
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore


def test_run_once_requires_settings_and_tokens(tmp_path):
    from app.web.services import run_once_for_user

    settings = _settings(tmp_path)
    db.init_db(settings.database_path)
    user = db.get_or_create_user(settings.database_path, "user@example.com")

    with pytest.raises(RuntimeError, match="settings"):
        run_once_for_user(settings, user["id"])

    db.save_settings(settings.database_path, user["id"], "source", "dest", 60)

    with pytest.raises(RuntimeError, match="token"):
        run_once_for_user(settings, user["id"])


def test_run_once_creates_jobs_and_completes_with_transcript_id(tmp_path, monkeypatch):
    from app.web import services

    settings = _settings(tmp_path)
    user = _user_with_settings_and_token(settings)
    fake_drive = FakeDrive([_drive_file("file-1", "meet.mp4")])
    deepgram_instances = []

    _patch_clients(monkeypatch, services, fake_drive, deepgram_instances)

    processed = services.run_once_for_user(settings, user["id"])

    jobs = db.list_jobs(settings.database_path, user["id"])
    assert processed == 1
    assert len(jobs) == 1
    assert jobs[0]["source_file_id"] == "file-1"
    assert jobs[0]["source_file_name"] == "meet.mp4"
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["transcript_drive_file_id"] == "txt-file-1"
    assert jobs[0]["error_message"] is None
    assert deepgram_instances[0].api_key == "global-dg-key"


def test_run_once_processes_only_files_for_requested_user(tmp_path, monkeypatch):
    from app.web import services

    settings = _settings(tmp_path)
    user_one = _user_with_settings_and_token(
        settings, email="one@example.com", source="source-one", destination="dest-one"
    )
    user_two = _user_with_settings_and_token(
        settings, email="two@example.com", source="source-two", destination="dest-two"
    )
    drives = {
        ("source-one", "dest-one"): FakeDrive([_drive_file("one-file", "one.mp4")]),
        ("source-two", "dest-two"): FakeDrive([_drive_file("two-file", "two.mp4")]),
    }

    def fake_from_credentials(credentials, source_folder_id, destination_folder_id):
        return drives[(source_folder_id, destination_folder_id)]

    monkeypatch.setattr(services, "build_oauth_credentials", lambda token: object())
    monkeypatch.setattr(services.DriveClient, "from_credentials", fake_from_credentials)
    monkeypatch.setattr(services, "DeepgramClient", FakeDeepgramClient)

    services.run_once_for_user(settings, user_two["id"])

    assert drives[("source-one", "dest-one")].downloads == []
    assert drives[("source-two", "dest-two")].downloads == ["two-file"]
    assert db.list_jobs(settings.database_path, user_one["id"]) == []
    assert [job["source_file_id"] for job in db.list_jobs(settings.database_path, user_two["id"])] == [
        "two-file"
    ]


def test_run_once_marks_failed_with_error_message(tmp_path, monkeypatch):
    from app.web import services

    settings = _settings(tmp_path)
    user = _user_with_settings_and_token(settings)
    fake_drive = FakeDrive([_drive_file("file-1", "meet.mp4")], fail_upload=True)

    _patch_clients(monkeypatch, services, fake_drive, [])

    processed = services.run_once_for_user(settings, user["id"])

    jobs = db.list_jobs(settings.database_path, user["id"])
    assert processed == 0
    assert len(jobs) == 1
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["transcript_drive_file_id"] is None
    assert "upload failed" in jobs[0]["error_message"]


def test_build_oauth_credentials_maps_web_token_format():
    from app.web.services import build_oauth_credentials

    credentials = build_oauth_credentials(
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": "https://www.googleapis.com/auth/drive",
            "expiry": "2026-06-03T00:00:00+00:00",
        }
    )

    assert credentials.token == "access-token"
    assert credentials.refresh_token == "refresh-token"


def _patch_clients(monkeypatch, services, fake_drive, deepgram_instances):
    monkeypatch.setattr(services, "build_oauth_credentials", lambda token: object())
    monkeypatch.setattr(
        services.DriveClient,
        "from_credentials",
        lambda credentials, source_folder_id, destination_folder_id: fake_drive,
    )

    class RecordingDeepgramClient(FakeDeepgramClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            deepgram_instances.append(self)

    monkeypatch.setattr(services, "DeepgramClient", RecordingDeepgramClient)


def _user_with_settings_and_token(
    settings: WebSettings,
    email: str = "user@example.com",
    source: str = "source",
    destination: str = "dest",
):
    db.init_db(settings.database_path)
    user = db.get_or_create_user(settings.database_path, email)
    db.save_settings(settings.database_path, user["id"], source, destination, 60)
    TokenStore(settings.database_path, fernet_from_secret(settings.app_secret_key)).save_for_user(
        user["id"],
        {
            "access_token": f"access-{email}",
            "refresh_token": f"refresh-{email}",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": "https://www.googleapis.com/auth/drive",
            "expiry": "2026-06-03T00:00:00+00:00",
        },
    )
    return user


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env(
        {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "secret",
            "APP_SECRET_KEY": "a-long-secret-for-tests",
            "SESSION_COOKIE_SECURE": "false",
            "GOOGLE_WEB_CLIENT_ID": "client-id",
            "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
            "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
            "DATABASE_URL": str(tmp_path / "app.db"),
            "DEEPGRAM_API_KEY": "global-dg-key",
            "TMP_DIR": str(tmp_path / "tmp"),
        }
    )


def _drive_file(file_id: str, name: str) -> DriveFile:
    return DriveFile(
        id=file_id,
        name=name,
        mime_type="video/mp4",
        size=10,
        created_time="2026-06-03T10:00:00Z",
        modified_time="2026-06-03T10:00:00Z",
    )


class FakeDrive:
    def __init__(self, files: list[DriveFile], fail_upload: bool = False):
        self.files = files
        self.fail_upload = fail_upload
        self.downloads = []

    def list_video_files(self) -> list[DriveFile]:
        return self.files

    def download_file(self, file: DriveFile, destination):
        self.downloads.append(file.id)
        destination.write_bytes(b"mp4 bytes")

    def upload_text_file(self, source_path, filename: str) -> str:
        if self.fail_upload:
            raise RuntimeError("upload failed")
        assert filename.endswith("_Transcricao.txt")
        assert "TRANSCRI" in source_path.read_text(encoding="utf-8")
        return "txt-file-1"


class FakeDeepgramClient:
    def __init__(self, **kwargs):
        self.api_key = kwargs["api_key"]

    def transcribe(self, video_path):
        assert video_path.read_bytes() == b"mp4 bytes"
        return {
            "results": {
                "utterances": [
                    {"start": 1.0, "speaker": 0, "transcript": "Texto transcrito."}
                ]
            }
        }
