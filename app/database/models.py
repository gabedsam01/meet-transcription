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
    transcript_drive_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # started_at is stamped when the worker claims a pending job (worker contract).
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
