"""Repository integration tests against real PostgreSQL (skipped if absent)."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.repositories import (
    DeepgramCredentialRepository,
    GoogleTokenRepository,
    TranscriptionJobRepository,
    TranscriptRepository,
    UserDriveSettingsRepository,
    UserRepository,
)


def test_user_create_sets_server_defaults(db):
    user = UserRepository(db).create(email="admin@example.com", name="Admin", role="admin")
    db.flush()

    assert user.id is not None
    assert user.role == "admin"
    assert user.is_active is True
    assert user.created_at is not None
    assert user.updated_at is not None


def test_user_get_and_get_by_email(db):
    repo = UserRepository(db)
    created = repo.create(email="a@example.com")
    db.flush()

    assert repo.get(created.id).email == "a@example.com"
    assert repo.get_by_email("a@example.com").id == created.id
    assert repo.get_by_email("missing@example.com") is None


def test_user_get_or_create_is_idempotent_and_updates_name(db):
    repo = UserRepository(db)
    first = repo.get_or_create(email="admin@example.com", name="Admin", role="admin")
    again = repo.get_or_create(email="admin@example.com", name="Renamed")

    assert first.id == again.id
    assert again.name == "Renamed"
    assert len(repo.list()) == 1


def test_ensure_admin_creates_promotes_and_is_idempotent(db):
    repo = UserRepository(db)
    # Promote an existing non-admin row.
    existing = repo.create(email="admin@example.com", role="user", is_active=False)
    db.flush()
    promoted = repo.ensure_admin(email="admin@example.com", name="Admin")
    assert promoted.id == existing.id
    assert promoted.role == "admin"
    assert promoted.is_active is True
    assert promoted.name == "Admin"
    # Idempotent.
    again = repo.ensure_admin(email="admin@example.com")
    assert again.id == existing.id and again.role == "admin"
    # Creates when absent.
    fresh = repo.ensure_admin(email="fresh@example.com")
    db.flush()
    assert fresh.role == "admin" and fresh.is_active is True
    assert len(repo.list()) == 2


def test_user_update_only_touches_allowed_fields(db):
    repo = UserRepository(db)
    user = repo.create(email="a@example.com")
    db.flush()

    updated = repo.update(user.id, name="New", is_active=False, email="hacked@example.com")

    assert updated.name == "New"
    assert updated.is_active is False
    assert updated.email == "a@example.com"  # email is not an allowed update field
    assert repo.update(999999, name="x") is None


def test_role_check_constraint_rejects_unknown_role(db):
    # create() flushes internally, so the constraint fires on the create call.
    with pytest.raises(IntegrityError):
        UserRepository(db).create(email="weird@example.com", role="superadmin")


def test_drive_settings_upsert_roundtrip(db):
    user = UserRepository(db).create(email="a@example.com")
    db.flush()
    repo = UserDriveSettingsRepository(db)

    created = repo.upsert_for_user(
        user.id, source_drive_folder_id="src", destination_drive_folder_id="dst"
    )
    updated = repo.upsert_for_user(user.id, source_drive_folder_id="src2")

    assert created.id == updated.id  # single row per user
    assert updated.source_drive_folder_id == "src2"
    assert updated.destination_drive_folder_id == "dst"
    assert updated.save_copy_to_drive is False
    assert repo.get_for_user(user.id).source_drive_folder_id == "src2"


def test_jobs_create_update_and_completion_stamps_processed_at(db):
    user = UserRepository(db).create(email="a@example.com")
    db.flush()
    repo = TranscriptionJobRepository(db)

    job = repo.create(user_id=user.id, status="pending")
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.processed_at is None

    repo.update(
        job.id,
        source_file_id="file1",
        source_file_name="m.mp4",
        status="completed",
        transcript_drive_file_id="txt1",
        attempts=1,
    )
    refreshed = repo.get(job.id)
    assert refreshed.status == "completed"
    assert refreshed.transcript_drive_file_id == "txt1"
    assert refreshed.processed_at is not None


def test_jobs_listing_is_newest_first_and_scoped_to_user(db):
    users = UserRepository(db)
    one = users.create(email="one@example.com")
    two = users.create(email="two@example.com")
    db.flush()
    repo = TranscriptionJobRepository(db)

    a = repo.create(user_id=one.id, status="pending")
    b = repo.create(user_id=one.id, status="pending")
    repo.create(user_id=two.id, status="pending")
    db.flush()

    listed = repo.list_for_user(one.id)
    assert [j.id for j in listed] == [b.id, a.id]
    assert len(repo.list_for_user(two.id)) == 1
    assert len(repo.latest_for_user(one.id, limit=1)) == 1


def test_active_job_returns_pending_or_processing_only(db):
    user = UserRepository(db).create(email="a@example.com")
    db.flush()
    repo = TranscriptionJobRepository(db)

    assert repo.get_active_for_user(user.id) is None

    repo.create(user_id=user.id, status="completed")
    db.flush()
    assert repo.get_active_for_user(user.id) is None

    pending = repo.create(user_id=user.id, status="pending")
    db.flush()
    assert repo.get_active_for_user(user.id).id == pending.id

    repo.update(pending.id, status="processing")
    assert repo.get_active_for_user(user.id).id == pending.id

    repo.update(pending.id, status="failed")
    assert repo.get_active_for_user(user.id) is None


def test_completed_dedupe_index_blocks_duplicate_completed_source(db):
    user = UserRepository(db).create(email="a@example.com")
    db.flush()
    repo = TranscriptionJobRepository(db)

    first = repo.create(user_id=user.id, status="pending", source_file_id="file1")
    repo.update(first.id, status="completed")
    db.flush()
    assert repo.has_completed_for_source(user.id, "file1") is True

    second = repo.create(user_id=user.id, status="pending", source_file_id="file1")
    with pytest.raises(IntegrityError):
        # update() flushes internally; the partial unique index rejects a second
        # completed job for the same (user_id, source_file_id).
        repo.update(second.id, status="completed")


def test_transcript_create_and_lookup(db):
    user = UserRepository(db).create(email="a@example.com")
    db.flush()
    job = TranscriptionJobRepository(db).create(user_id=user.id, status="completed")
    db.flush()
    repo = TranscriptRepository(db)

    transcript = repo.create(
        job_id=job.id, user_id=user.id, transcript_text="hello", transcript_json={"a": 1}
    )

    assert repo.get(transcript.id).transcript_text == "hello"
    assert repo.get_for_job(job.id).transcript_json == {"a": 1}
    assert [t.id for t in repo.list_for_user(user.id)] == [transcript.id]


def test_google_token_upsert_keeps_one_row_per_user(db):
    user = UserRepository(db).create(email="a@example.com")
    db.flush()
    repo = GoogleTokenRepository(db)

    repo.upsert_for_user(
        user.id,
        encrypted_access_token="a1",
        encrypted_refresh_token="r1",
        token_uri="uri",
        client_id="cid",
        client_secret="cs",
        scopes=["scope-a"],
        expiry=None,
    )
    repo.upsert_for_user(
        user.id,
        encrypted_access_token="a2",
        encrypted_refresh_token=None,
        token_uri="uri",
        client_id="cid",
        client_secret=None,
        scopes=["scope-a", "scope-b"],
        expiry=None,
    )

    token = repo.get_for_user(user.id)
    assert token.encrypted_access_token == "a2"
    assert token.encrypted_refresh_token is None
    assert token.scopes == ["scope-a", "scope-b"]


def test_deepgram_credential_upsert(db):
    user = UserRepository(db).create(email="a@example.com")
    db.flush()
    repo = DeepgramCredentialRepository(db)

    repo.upsert_for_user(user.id, encrypted_api_key="enc-1")
    repo.upsert_for_user(user.id, encrypted_api_key="enc-2")

    assert repo.get_for_user(user.id).encrypted_api_key == "enc-2"
