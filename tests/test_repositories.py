import pytest

from app.web.repositories import (
    DriveSettings,
    GoogleToken,
    Job,
    RepositoryBackendUnavailable,
    User,
    build_repositories,
)


def test_domain_dataclasses_are_constructible():
    user = User(id=1, email="a@b.com", name="A", role="admin", is_active=True)
    assert user.google_email is None
    token = GoogleToken(
        access_token="x",
        refresh_token=None,
        token_uri="u",
        client_id="c",
        client_secret=None,
        scopes="s",
        expiry=None,
    )
    assert token.access_token == "x"
    ds = DriveSettings(
        source_drive_folder_url="url",
        source_drive_folder_id="id",
        destination_drive_folder_url=None,
        destination_drive_folder_id=None,
        save_copy_to_drive=False,
    )
    assert ds.source_drive_folder_id == "id"
    job = Job(id=1, user_id=1, status="pending")
    assert job.attempts == 0 and job.source_file_id is None


def test_build_repositories_raises_when_postgres_backend_absent():
    class S:
        database_url = "postgresql://nope"

    with pytest.raises(RepositoryBackendUnavailable):
        build_repositories(S())
