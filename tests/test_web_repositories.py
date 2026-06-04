"""Contract tests for the web repository layer (``app/web/repositories.py``).

These cover the storage-agnostic domain dataclasses and the DI entrypoint that
wires the web app onto the postgres-core backend. Real persistence behaviour is
covered by ``tests/test_repositories.py`` (against PostgreSQL) and
``tests/test_auth_bridge.py``.
"""

import app.db.postgres as pg_module
from app.web.repositories import (
    DriveSettings,
    GoogleToken,
    Job,
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


def test_build_repositories_delegates_to_postgres_core(monkeypatch):
    """The web DI entrypoint forwards the configured DSN to postgres-core.

    postgres-core is now integrated, so ``build_repositories`` no longer raises
    ``RepositoryBackendUnavailable``; it delegates to
    ``app.db.postgres.build_repositories(database_url)``.
    """
    captured = {}

    def fake_build(database_url):
        captured["url"] = database_url
        return "SENTINEL_BUNDLE"

    monkeypatch.setattr(pg_module, "build_repositories", fake_build)

    class S:
        database_url = "postgresql+psycopg://app:app@db:5432/meet"

    result = build_repositories(S())
    assert result == "SENTINEL_BUNDLE"
    assert captured["url"] == "postgresql+psycopg://app:app@db:5432/meet"
