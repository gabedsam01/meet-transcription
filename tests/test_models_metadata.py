"""Pure-logic tests for the ORM metadata (no database needed)."""

from sqlalchemy.dialects.postgresql import JSONB

from app.database.models import Base, TranscriptionJob


def test_metadata_defines_all_expected_tables():
    expected = {
        "users",
        "google_tokens",
        "deepgram_credentials",
        "user_drive_settings",
        "transcription_jobs",
        "transcripts",
    }
    assert expected <= set(Base.metadata.tables)


def test_users_table_has_auth_columns():
    columns = Base.metadata.tables["users"].columns
    assert {"id", "email", "name", "password_hash", "role", "is_active",
            "created_at", "updated_at"} <= set(columns.keys())
    assert columns["email"].unique is True
    assert columns["email"].nullable is False


def test_google_tokens_use_encrypted_columns_and_jsonb_scopes():
    columns = Base.metadata.tables["google_tokens"].columns
    assert "encrypted_access_token" in columns
    assert "encrypted_refresh_token" in columns
    assert "access_token" not in columns  # renamed away from the old SQLite schema
    assert isinstance(columns["scopes"].type, JSONB)


def test_user_drive_settings_has_no_poll_interval():
    columns = Base.metadata.tables["user_drive_settings"].columns
    assert "poll_interval_seconds" not in columns
    assert {"source_drive_folder_id", "destination_drive_folder_id",
            "save_copy_to_drive"} <= set(columns.keys())


def test_transcripts_table_is_append_only():
    columns = Base.metadata.tables["transcripts"].columns
    assert "created_at" in columns
    assert "updated_at" not in columns
    assert isinstance(columns["transcript_json"].type, JSONB)


def test_completed_dedupe_partial_unique_index_exists():
    indexes = {idx.name: idx for idx in TranscriptionJob.__table__.indexes}
    dedupe = indexes["uq_transcription_jobs_completed_source"]
    assert dedupe.unique is True
    # Partial: only constrains rows whose status is 'completed'.
    assert dedupe.dialect_options["postgresql"]["where"] is not None
