"""SQLAlchemy ORM models for the PostgreSQL schema.

The schema is PostgreSQL-only: JSONB columns and a partial unique index are
used directly. Timestamps are timezone-aware and default to ``now()`` on the
server.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

VALID_ROLES = ("admin", "user")


class Base(DeclarativeBase):
    """Declarative base for all models."""


class TimestampMixin:
    """``created_at``/``updated_at`` columns, server-defaulted to ``now()``."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'user'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Google identity captured at OAuth connect time (consumed by the auth branch).
    google_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class GoogleToken(TimestampMixin, Base):
    __tablename__ = "google_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_uri: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeepgramCredential(TimestampMixin, Base):
    __tablename__ = "deepgram_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderCredential(TimestampMixin, Base):
    """Per-user, per-provider encrypted API key (Models tab).

    Supersedes the single-provider ``deepgram_credentials`` table: a user has at
    most one key per provider. ``encrypted_api_key`` is always Fernet ciphertext —
    plaintext keys never reach this layer. The legacy ``deepgram_credentials``
    table is kept for backward-compatible reads; migration 0002 copies its rows
    here with ``provider='deepgram'``.
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_provider_credentials_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)


class UserModelSettings(TimestampMixin, Base):
    """Per-user transcription model selection (Models tab).

    One row per user (``user_id`` unique). ``primary_*`` is the chosen provider +
    model; ``fallback_*`` an optional second provider used when the primary's
    credential is missing; ``local_*`` mirrors the env-driven local engine for the
    UI. All values are validated/clamped by ``provider_config.normalize_model_settings``
    before use, so a stale row can never crash the worker.
    """

    __tablename__ = "user_model_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    primary_provider: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'deepgram'")
    )
    primary_model: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'nova-3'")
    )
    fallback_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    fallback_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_engine: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_quantization: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserDriveSettings(TimestampMixin, Base):
    __tablename__ = "user_drive_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    source_drive_folder_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_drive_folder_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_drive_folder_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination_drive_folder_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination_drive_folder_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination_drive_folder_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    save_copy_to_drive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class TranscriptionJob(TimestampMixin, Base):
    __tablename__ = "transcription_jobs"
    __table_args__ = (
        Index("ix_transcription_jobs_user_status", "user_id", "status"),
        Index("ix_transcription_jobs_user_source", "user_id", "source_file_id"),
        # Retry sweep: claim/list filter pending rows by next_retry_at.
        Index("ix_transcription_jobs_status_next_retry", "status", "next_retry_at"),
        # Per-user daily-job counting (guardrails) and user job listings.
        Index("ix_transcription_jobs_user_created", "user_id", "created_at"),
        # Dedupe: a user cannot have two completed jobs for the same source file.
        Index(
            "uq_transcription_jobs_completed_source",
            "user_id",
            "source_file_id",
            unique=True,
            postgresql_where=text("status = 'completed'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Machine error code (RATE_LIMIT/KEY_INVALID/...) set by the retry policy.
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When a retryable failure may be re-claimed; NULL means immediately eligible.
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transcript_drive_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # started_at is stamped when the worker claims a pending job (worker contract).
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserAutomationSettings(TimestampMixin, Base):
    """Per-user auto-poll configuration, status, and cost guardrails.

    NULL guardrail columns fall back to the global env defaults. ``last_*`` track
    the most recent poll for the UI ("última verificação"/"último erro").
    """

    __tablename__ = "user_automation_settings"
    __table_args__ = (
        Index("ix_user_automation_enabled_polled", "auto_poll_enabled", "last_poll_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    auto_poll_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    poll_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("300")
    )
    max_files_per_poll: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    last_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cost/quota guardrails (NULL = use the global env default / unlimited).
    daily_jobs_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_file_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_cloud_minutes_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_file_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"

    # Transcripts are immutable records: only created_at, no updated_at.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("transcription_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transcript_text: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Drive file id of the uploaded transcript (worker contract).
    drive_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserExtensionToken(Base):
    """Per-user upload token for the Chrome extension.

    One user may have many tokens (rotate, label per device); each one is a
    separate credential that authenticates the extension on behalf of that user.
    The real token is shown to the user EXACTLY ONCE at creation time; we
    persist only ``token_hash`` (SHA-256 + server-side pepper) and a short
    ``token_prefix`` used to render the masked display. ``revoked_at`` makes
    revocations a soft-delete (the hash stays so an old client gets a clean
    ``invalid_token`` rather than 404).
    """

    __tablename__ = "user_extension_tokens"
    __table_args__ = (
        Index("ix_user_extension_tokens_user", "user_id"),
        Index("ix_user_extension_tokens_prefix", "token_prefix"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Hex SHA-256 of (raw_token + server pepper). Never reversed.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # First chars of the raw token, used for the masked list (e.g. "mtrec_a1b2…").
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
