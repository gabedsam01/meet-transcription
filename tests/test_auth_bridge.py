"""Integration tests for the auth-branch bridge (app/db/postgres.py)."""

import pytest

from app.database.connection import DatabaseConfigError
from app.db._auth_contract import DriveSettings, GoogleToken
from app.db.postgres import build_repositories


def test_build_repositories_without_url_or_env_fails_clearly(monkeypatch):
    # Symmetric with build_postgres_repositories: None falls back to DATABASE_URL,
    # and a missing DATABASE_URL raises a clear, actionable error (no connection).
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DatabaseConfigError, match="DATABASE_URL is required"):
        build_repositories()


def test_build_repositories_returns_full_bundle(pg):
    bundle = build_repositories(engine=pg)
    for attr in ("users", "google_tokens", "deepgram_credentials", "drive_settings", "jobs"):
        assert hasattr(bundle, attr)


def test_users_ensure_admin_is_idempotent(pg):
    users = build_repositories(engine=pg).users
    admin = users.ensure_admin(email="admin@example.com", password_hash="h1")
    assert admin.role == "admin"
    assert admin.is_active is True

    again = users.ensure_admin(email="admin@example.com", password_hash="ignored")
    assert again.id == admin.id
    assert users.get_password_hash(admin.id) == "h1"  # password not overwritten


def test_users_lookups_create_and_mutations(pg):
    users = build_repositories(engine=pg).users
    user = users.create(email="u@example.com", password_hash="h", role="user", name="U")
    assert user.role == "user" and user.name == "U"

    assert users.get_by_email("u@example.com").id == user.id
    assert users.get_by_id(user.id).email == "u@example.com"
    assert users.get_by_email("missing@example.com") is None

    users.set_active(user.id, False)
    assert users.get_by_id(user.id).is_active is False
    users.set_password_hash(user.id, "h2")
    assert users.get_password_hash(user.id) == "h2"
    assert "u@example.com" in {u.email for u in users.list_all()}


def test_users_set_google_identity(pg):
    users = build_repositories(engine=pg).users
    user = users.create(email="g@example.com", password_hash="h", role="user")
    users.set_google_identity(user.id, "gmail@example.com", "Google Name")

    got = users.get_by_id(user.id)
    assert got.google_email == "gmail@example.com"
    assert got.google_name == "Google Name"


def test_google_tokens_keep_ciphertext_and_string_scopes(pg):
    bundle = build_repositories(engine=pg)
    user = bundle.users.create(email="t@example.com", password_hash="h", role="user")
    bundle.google_tokens.save_for_user(
        user.id,
        GoogleToken(
            access_token="CIPHER_a",
            refresh_token="CIPHER_r",
            token_uri="uri",
            client_id="cid",
            client_secret="CIPHER_cs",
            scopes="https://www.googleapis.com/auth/drive",
            expiry="2026-06-03T10:00:00Z",
        ),
    )

    tok = bundle.google_tokens.get_for_user(user.id)
    assert tok.access_token == "CIPHER_a"
    assert tok.refresh_token == "CIPHER_r"
    assert tok.client_secret == "CIPHER_cs"
    assert isinstance(tok.scopes, str)
    assert tok.scopes == "https://www.googleapis.com/auth/drive"
    assert bundle.google_tokens.get_for_user(999999) is None


def test_google_tokens_empty_scopes_is_empty_string(pg):
    bundle = build_repositories(engine=pg)
    user = bundle.users.create(email="es@example.com", password_hash="h", role="user")
    bundle.google_tokens.save_for_user(
        user.id,
        GoogleToken(
            access_token="a",
            refresh_token=None,
            token_uri="uri",
            client_id="cid",
            client_secret=None,
            scopes="",
            expiry=None,
        ),
    )

    tok = bundle.google_tokens.get_for_user(user.id)
    assert tok.scopes == ""  # empty -> empty string at the auth border


def test_drive_settings_roundtrip(pg):
    bundle = build_repositories(engine=pg)
    user = bundle.users.create(email="d@example.com", password_hash="h", role="user")
    bundle.drive_settings.save_for_user(
        user.id,
        DriveSettings(
            source_drive_folder_url="su",
            source_drive_folder_id="sid",
            destination_drive_folder_url="du",
            destination_drive_folder_id="did",
            save_copy_to_drive=True,
        ),
    )

    ds = bundle.drive_settings.get_for_user(user.id)
    assert ds.source_drive_folder_id == "sid"
    assert ds.destination_drive_folder_url == "du"
    assert ds.save_copy_to_drive is True
    assert bundle.drive_settings.get_for_user(999999) is None


def test_deepgram_credentials_store_ciphertext(pg):
    bundle = build_repositories(engine=pg)
    user = bundle.users.create(email="dg@example.com", password_hash="h", role="user")
    assert bundle.deepgram_credentials.get_encrypted_for_user(user.id) is None

    bundle.deepgram_credentials.save_for_user(user.id, "ENC_KEY")
    assert bundle.deepgram_credentials.get_encrypted_for_user(user.id) == "ENC_KEY"


def test_jobs_create_list_active_with_string_timestamps(pg):
    bundle = build_repositories(engine=pg)
    user = bundle.users.create(email="j@example.com", password_hash="h", role="user")
    job = bundle.jobs.create_job(
        user_id=user.id, source_file_id="f1", source_file_name="m.mp4"
    )
    assert job.status == "pending"
    assert isinstance(job.created_at, str)  # auth shape: ISO strings

    assert bundle.jobs.find_active_for_user(user.id).id == job.id
    jobs = bundle.jobs.list_jobs_for_user(user.id)
    assert [j.id for j in jobs] == [job.id]
    assert len(bundle.jobs.list_jobs_for_user(user.id, limit=1)) == 1
