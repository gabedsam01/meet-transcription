from pathlib import Path

from app.drive_client import build_drive_credentials, is_ready_video_file, sort_drive_files, to_drive_file


def test_is_ready_video_file_accepts_mp4_mime_type():
    assert is_ready_video_file({"name": "meeting", "mimeType": "video/mp4", "size": "10"}) is True


def test_is_ready_video_file_rejects_non_mp4_video_type():
    assert is_ready_video_file({"name": "meeting.mov", "mimeType": "video/quicktime", "size": "10"}) is False


def test_is_ready_video_file_accepts_mp4_extension_when_mime_type_is_generic():
    assert is_ready_video_file({"name": "meeting.mp4", "mimeType": "application/octet-stream", "size": "10"}) is True


def test_is_ready_video_file_rejects_trashed_or_zero_size_files():
    assert is_ready_video_file({"name": "meeting.mp4", "mimeType": "video/mp4", "trashed": True, "size": "10"}) is False
    assert is_ready_video_file({"name": "meeting.mp4", "mimeType": "video/mp4", "size": "0"}) is False


def test_to_drive_file_maps_google_payload():
    file = to_drive_file(
        {
            "id": "abc",
            "name": "meeting.mp4",
            "mimeType": "video/mp4",
            "size": "10",
            "createdTime": "2026-06-03T10:00:00Z",
            "modifiedTime": "2026-06-03T10:05:00Z",
        }
    )

    assert file.id == "abc"
    assert file.name == "meeting.mp4"
    assert file.size == 10


def test_sort_drive_files_orders_oldest_first():
    newer = to_drive_file({"id": "2", "name": "b.mp4", "mimeType": "video/mp4", "size": "10", "createdTime": "2026-06-03T11:00:00Z", "modifiedTime": "2026-06-03T11:00:00Z"})
    older = to_drive_file({"id": "1", "name": "a.mp4", "mimeType": "video/mp4", "size": "10", "createdTime": "2026-06-03T10:00:00Z", "modifiedTime": "2026-06-03T10:00:00Z"})

    assert [file.id for file in sort_drive_files([newer, older])] == ["1", "2"]


def test_build_drive_credentials_uses_service_account_mode():
    settings = FakeSettings(google_auth_mode="service_account")
    fake_service_account = FakeServiceAccountCredentials()

    credentials = build_drive_credentials(
        settings,
        service_account_credentials_cls=fake_service_account,
    )

    assert credentials == "service-account-credentials"
    assert fake_service_account.calls == [
        ("/app/secrets/service-account.json", ["https://www.googleapis.com/auth/drive"])
    ]


def test_build_drive_credentials_uses_oauth_token_and_refreshes(tmp_path):
    token_file = tmp_path / "token.json"
    client_secrets_file = tmp_path / "oauth-client.json"
    client_secrets_file.write_text("{}", encoding="utf-8")
    settings = FakeSettings(
        google_auth_mode="oauth",
        google_oauth_token_file=token_file,
        google_oauth_client_secrets_file=client_secrets_file,
    )
    fake_oauth = FakeOAuthCredentials(expired=True, refresh_token="refresh-token")

    credentials = build_drive_credentials(
        settings,
        oauth_credentials_cls=fake_oauth,
        request_factory=lambda: "request",
    )

    assert credentials is fake_oauth.credentials
    assert fake_oauth.calls == [
        (str(token_file), ["https://www.googleapis.com/auth/drive"])
    ]
    assert fake_oauth.credentials.refresh_calls == ["request"]
    assert token_file.read_text(encoding="utf-8") == '{"token":"refreshed"}'


def test_build_drive_credentials_requires_oauth_client_secrets_file(tmp_path):
    settings = FakeSettings(
        google_auth_mode="oauth",
        google_oauth_token_file=tmp_path / "token.json",
        google_oauth_client_secrets_file=tmp_path / "missing-oauth-client.json",
    )

    try:
        build_drive_credentials(settings, oauth_credentials_cls=FakeOAuthCredentials(False, None))
    except FileNotFoundError as exc:
        assert "missing-oauth-client.json" in str(exc)
    else:
        raise AssertionError("Expected missing oauth-client.json to fail fast")


class FakeSettings:
    def __init__(
        self,
        google_auth_mode: str,
        google_oauth_token_file: Path | None = None,
        google_oauth_client_secrets_file: Path | None = None,
    ):
        self.google_auth_mode = google_auth_mode
        self.google_service_account_file = Path("/app/secrets/service-account.json")
        self.google_oauth_token_file = google_oauth_token_file or Path("/app/secrets/token.json")
        self.google_oauth_client_secrets_file = google_oauth_client_secrets_file or Path("/app/secrets/oauth-client.json")


class FakeServiceAccountCredentials:
    def __init__(self):
        self.calls = []

    def from_service_account_file(self, path, scopes):
        self.calls.append((path, scopes))
        return "service-account-credentials"


class FakeOAuthCredentials:
    def __init__(self, expired: bool, refresh_token: str | None):
        self.calls = []
        self.credentials = FakeCredentials(expired, refresh_token)

    def from_authorized_user_file(self, path, scopes):
        self.calls.append((path, scopes))
        return self.credentials


class FakeCredentials:
    def __init__(self, expired: bool, refresh_token: str | None):
        self.expired = expired
        self.refresh_token = refresh_token
        self.refresh_calls = []

    def refresh(self, request):
        self.refresh_calls.append(request)

    def to_json(self):
        return '{"token":"refreshed"}'


def test_download_by_id_writes_media_to_destination(tmp_path, monkeypatch):
    import app.drive_client as drive_module
    from app.drive_client import DriveClient

    class FakeChunkDownloader:
        def __init__(self, handle, request):
            self.handle = handle
            self.request = request
            self.done = False

        def next_chunk(self):
            self.handle.write(b"video-bytes")
            self.done = True
            return None, True

    monkeypatch.setattr(drive_module, "MediaIoBaseDownload", FakeChunkDownloader, raising=False)

    captured = {}

    class FakeFiles:
        def get_media(self, fileId, supportsAllDrives):
            captured["file_id"] = fileId
            captured["all_drives"] = supportsAllDrives
            return "request-object"

    class FakeService:
        def files(self):
            return FakeFiles()

    client = DriveClient.__new__(DriveClient)
    client.service = FakeService()
    destination = tmp_path / "out" / "video.mp4"

    client.download_by_id("file-123", destination)

    assert captured == {"file_id": "file-123", "all_drives": True}
    assert destination.read_bytes() == b"video-bytes"
