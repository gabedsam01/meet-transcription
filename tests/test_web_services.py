from __future__ import annotations

from app.database.repositories import (
    TranscriptionJobRepository,
    UserDriveSettingsRepository,
    UserRepository,
)
from app.processor import DriveFile
from app.web import services
from app.web.config import WebSettings
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore

_SECRET = "a-long-secret-for-tests"


# --- enqueue_run_once_job (fast request path, no processing) ----------------


def test_enqueue_reports_missing_settings(db, tmp_path):
    settings = _settings(tmp_path)
    user = UserRepository(db).create(email="user@example.com")
    db.flush()

    result = services.enqueue_run_once_job(settings, db, user.id)

    assert result.status == "missing_settings"
    assert list(TranscriptionJobRepository(db).list_for_user(user.id)) == []


def test_enqueue_reports_google_not_connected(db, tmp_path):
    settings = _settings(tmp_path)
    user = UserRepository(db).create(email="user@example.com")
    UserDriveSettingsRepository(db).upsert_for_user(
        user.id, source_drive_folder_id="source", destination_drive_folder_id="dest"
    )
    db.flush()

    result = services.enqueue_run_once_job(settings, db, user.id)

    assert result.status == "not_connected"
    assert list(TranscriptionJobRepository(db).list_for_user(user.id)) == []


def test_enqueue_creates_pending_job_without_processing(db, tmp_path):
    settings = _settings(tmp_path)
    user_id = _seed_user(db)

    # No Drive/Deepgram clients are patched here. If enqueue tried to process
    # synchronously it would build a real DriveClient and fail/hang.
    result = services.enqueue_run_once_job(settings, db, user_id)

    assert result.status == "created"
    assert result.job.status == "pending"
    jobs = list(TranscriptionJobRepository(db).list_for_user(user_id))
    assert len(jobs) == 1
    assert jobs[0].status == "pending"
    assert jobs[0].source_file_id is None


def test_enqueue_blocks_when_active_job_exists(db, tmp_path):
    settings = _settings(tmp_path)
    user_id = _seed_user(db)
    TranscriptionJobRepository(db).create(user_id=user_id, status="processing")
    db.flush()

    result = services.enqueue_run_once_job(settings, db, user_id)

    assert result.status == "already_running"
    assert len(list(TranscriptionJobRepository(db).list_for_user(user_id))) == 1


# --- run_user_job_background (heavy work, runs after the response) ----------


def test_background_job_completes_and_records_file_and_transcript(db, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user_id = _seed_user(db)
    job = TranscriptionJobRepository(db).create(user_id=user_id, status="pending")
    db.commit()
    fake_drive = FakeDrive([_drive_file("file-1", "meet.mp4")])
    deepgram_instances = []
    _patch_clients(monkeypatch, fake_drive, deepgram_instances)

    services.run_user_job_background(settings, job.id, user_id)

    db.expire_all()
    jobs = list(TranscriptionJobRepository(db).list_for_user(user_id))
    assert len(jobs) == 1
    assert jobs[0].status == "completed"
    assert jobs[0].source_file_id == "file-1"
    assert jobs[0].source_file_name == "meet.mp4"
    assert jobs[0].transcript_drive_file_id == "txt-file-1"
    assert jobs[0].error_message is None
    assert jobs[0].attempts == 1
    assert jobs[0].processed_at is not None
    assert deepgram_instances[0].api_key == "global-dg-key"


def test_background_job_skips_source_already_completed(db, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user_id = _seed_user(db)
    repo = TranscriptionJobRepository(db)
    # A prior completed job for the same source file.
    prior = repo.create(user_id=user_id, status="pending", source_file_id="file-1")
    repo.update(prior.id, status="completed")
    job = repo.create(user_id=user_id, status="pending")
    db.commit()
    fake_drive = FakeDrive([_drive_file("file-1", "meet.mp4")])
    deepgram_instances = []
    _patch_clients(monkeypatch, fake_drive, deepgram_instances)

    services.run_user_job_background(settings, job.id, user_id)

    db.expire_all()
    done = TranscriptionJobRepository(db).get(job.id)
    assert done.status == "skipped"  # not "failed"
    assert done.error_message == "Source file already completed for this user."
    assert done.source_file_id == "file-1"
    # No heavy work happened: no download, no Deepgram client built, no upload.
    assert fake_drive.downloads == []
    assert deepgram_instances == []


def test_background_job_marks_failed_with_error_message(db, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user_id = _seed_user(db)
    job = TranscriptionJobRepository(db).create(user_id=user_id, status="pending")
    db.commit()
    fake_drive = FakeDrive([_drive_file("file-1", "meet.mp4")], fail_upload=True)
    _patch_clients(monkeypatch, fake_drive, [])

    services.run_user_job_background(settings, job.id, user_id)

    db.expire_all()
    jobs = list(TranscriptionJobRepository(db).list_for_user(user_id))
    assert jobs[0].status == "failed"
    assert jobs[0].transcript_drive_file_id is None
    assert "upload failed" in jobs[0].error_message


def test_background_job_marks_failed_when_no_video_files(db, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user_id = _seed_user(db)
    job = TranscriptionJobRepository(db).create(user_id=user_id, status="pending")
    db.commit()
    _patch_clients(monkeypatch, FakeDrive([]), [])

    services.run_user_job_background(settings, job.id, user_id)

    db.expire_all()
    jobs = list(TranscriptionJobRepository(db).list_for_user(user_id))
    assert jobs[0].status == "failed"
    assert "No video files" in jobs[0].error_message


def test_background_job_never_stays_processing_on_error(db, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user_id = _seed_user(db)
    job = TranscriptionJobRepository(db).create(user_id=user_id, status="pending")
    db.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("drive exploded")

    monkeypatch.setattr(services, "build_oauth_credentials", lambda token: object())
    monkeypatch.setattr(services.DriveClient, "from_credentials", boom)
    monkeypatch.setattr(services, "DeepgramClient", FakeDeepgramClient)

    services.run_user_job_background(settings, job.id, user_id)

    db.expire_all()
    jobs = list(TranscriptionJobRepository(db).list_for_user(user_id))
    assert jobs[0].status == "failed"
    assert "drive exploded" in jobs[0].error_message


def test_background_job_uses_only_requested_users_drive(db, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    user_one = _seed_user(db, email="one@example.com", source="source-one", destination="dest-one")
    user_two = _seed_user(db, email="two@example.com", source="source-two", destination="dest-two")
    drives = {
        ("source-one", "dest-one"): FakeDrive([_drive_file("one-file", "one.mp4")]),
        ("source-two", "dest-two"): FakeDrive([_drive_file("two-file", "two.mp4")]),
    }

    def fake_from_credentials(credentials, source_folder_id, destination_folder_id):
        return drives[(source_folder_id, destination_folder_id)]

    monkeypatch.setattr(services, "build_oauth_credentials", lambda token: object())
    monkeypatch.setattr(services.DriveClient, "from_credentials", fake_from_credentials)
    monkeypatch.setattr(services, "DeepgramClient", FakeDeepgramClient)

    job = TranscriptionJobRepository(db).create(user_id=user_two, status="pending")
    db.commit()
    services.run_user_job_background(settings, job.id, user_two)

    db.expire_all()
    assert drives[("source-one", "dest-one")].downloads == []
    assert drives[("source-two", "dest-two")].downloads == ["two-file"]
    assert list(TranscriptionJobRepository(db).list_for_user(user_one)) == []
    assert [
        j.source_file_id for j in TranscriptionJobRepository(db).list_for_user(user_two)
    ] == ["two-file"]


def test_build_oauth_credentials_maps_web_token_format():
    """Pure logic: token dict -> google Credentials, no database."""
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


# --- helpers ----------------------------------------------------------------


def _patch_clients(monkeypatch, fake_drive, deepgram_instances):
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


def _token_data(email: str) -> dict:
    return {
        "access_token": f"access-{email}",
        "refresh_token": f"refresh-{email}",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": "https://www.googleapis.com/auth/drive",
        "expiry": "2026-06-03T00:00:00+00:00",
    }


def _seed_user(db, email: str = "user@example.com", source: str = "source", destination: str = "dest"):
    user = UserRepository(db).create(email=email)
    UserDriveSettingsRepository(db).upsert_for_user(
        user.id, source_drive_folder_id=source, destination_drive_folder_id=destination
    )
    TokenStore(fernet_from_secret(_SECRET)).save_for_user(db, user.id, _token_data(email))
    db.commit()
    return user.id


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env(
        {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "secret",
            "APP_SECRET_KEY": _SECRET,
            "SESSION_COOKIE_SECURE": "false",
            "GOOGLE_WEB_CLIENT_ID": "client-id",
            "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
            "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
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
