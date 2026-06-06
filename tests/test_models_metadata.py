"""Pure-logic tests for the ORM metadata (no database needed)."""

from sqlalchemy.dialects.postgresql import JSONB

from app.database.models import Base, TranscriptionJob, UserAutomationSettings


def test_metadata_defines_all_expected_tables():
    expected = {
        "users",
        "google_tokens",
        "deepgram_credentials",
        "provider_credentials",
        "user_model_settings",
        "user_drive_settings",
        "transcription_jobs",
        "transcripts",
    }
    assert expected <= set(Base.metadata.tables)


def test_provider_credentials_encrypted_and_unique_per_provider():
    table = Base.metadata.tables["provider_credentials"]
    columns = set(table.columns.keys())
    assert {"user_id", "provider", "encrypted_api_key"} <= columns
    assert "api_key" not in columns  # only ciphertext at rest
    constraint_names = {c.name for c in table.constraints}
    assert "uq_provider_credentials_user_provider" in constraint_names


def test_user_model_settings_has_provider_and_fallback_columns():
    columns = set(Base.metadata.tables["user_model_settings"].columns.keys())
    assert {
        "user_id",
        "primary_provider",
        "primary_model",
        "fallback_enabled",
        "fallback_provider",
        "fallback_model",
        "local_engine",
        "local_model",
        "local_quantization",
    } <= columns


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


def test_job_has_retry_columns():
    columns = TranscriptionJob.__table__.c
    assert "next_retry_at" in columns
    assert "last_error_code" in columns
    assert columns["next_retry_at"].nullable is True
    assert columns["last_error_code"].nullable is True


def test_job_has_retry_and_created_indexes():
    names = {idx.name for idx in TranscriptionJob.__table__.indexes}
    assert "ix_transcription_jobs_status_next_retry" in names
    assert "ix_transcription_jobs_user_created" in names


def test_automation_settings_table_defined():
    assert "user_automation_settings" in Base.metadata.tables
    columns = set(UserAutomationSettings.__table__.c.keys())
    expected = {
        "id", "user_id", "auto_poll_enabled", "poll_interval_seconds",
        "max_files_per_poll", "last_poll_at", "last_success_at",
        "last_error_code", "last_error_message", "daily_jobs_limit",
        "max_file_size_mb", "monthly_cloud_minutes_limit",
        "max_file_duration_minutes", "created_at", "updated_at",
    }
    assert expected <= columns


def test_automation_settings_user_id_unique_and_poll_index():
    columns = UserAutomationSettings.__table__.c
    assert columns["user_id"].unique is True
    names = {idx.name for idx in UserAutomationSettings.__table__.indexes}
    assert "ix_user_automation_enabled_polled" in names
