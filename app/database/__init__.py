"""PostgreSQL data layer: models, repositories, engine and session helpers."""

from __future__ import annotations

from app.database.connection import (
    DatabaseConfigError,
    create_database_engine,
    get_database_url,
)
from app.database.models import (
    Base,
    DeepgramCredential,
    GoogleToken,
    Transcript,
    TranscriptionJob,
    User,
    UserDriveSettings,
)
from app.database.repositories import (
    DeepgramCredentialRepository,
    GoogleTokenRepository,
    TranscriptionJobRepository,
    TranscriptRepository,
    UserDriveSettingsRepository,
    UserRepository,
)
from app.database.session import (
    get_db,
    get_engine,
    get_sessionmaker,
    init_engine,
    reset_engine,
    session_scope,
)

__all__ = [
    "DatabaseConfigError",
    "get_database_url",
    "create_database_engine",
    "Base",
    "User",
    "GoogleToken",
    "DeepgramCredential",
    "UserDriveSettings",
    "TranscriptionJob",
    "Transcript",
    "UserRepository",
    "GoogleTokenRepository",
    "DeepgramCredentialRepository",
    "UserDriveSettingsRepository",
    "TranscriptionJobRepository",
    "TranscriptRepository",
    "init_engine",
    "get_engine",
    "get_sessionmaker",
    "reset_engine",
    "get_db",
    "session_scope",
]
