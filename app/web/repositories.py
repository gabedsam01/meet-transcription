from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str | None
    role: str  # "admin" | "user"
    is_active: bool
    google_email: str | None = None
    google_name: str | None = None


@dataclass(frozen=True)
class GoogleToken:
    """Sensitive fields hold ciphertext at the repository boundary."""

    access_token: str
    refresh_token: str | None
    token_uri: str
    client_id: str
    client_secret: str | None
    scopes: str
    expiry: str | None


@dataclass(frozen=True)
class DriveSettings:
    source_drive_folder_url: str
    source_drive_folder_id: str
    destination_drive_folder_url: str | None
    destination_drive_folder_id: str | None
    save_copy_to_drive: bool


@dataclass(frozen=True)
class Job:
    id: int
    user_id: int
    status: str
    source_file_id: str | None = None
    source_file_name: str | None = None
    transcript_drive_file_id: str | None = None
    error_message: str | None = None
    attempts: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    processed_at: str | None = None


class UsersRepository(Protocol):
    def get_by_email(self, email: str) -> User | None: ...
    def get_by_id(self, user_id: int) -> User | None: ...
    def get_password_hash(self, user_id: int) -> str | None: ...
    def list_all(self) -> list[User]: ...
    def create(self, *, email: str, password_hash: str, role: str, name: str | None = None) -> User: ...
    def set_active(self, user_id: int, active: bool) -> None: ...
    def set_password_hash(self, user_id: int, password_hash: str) -> None: ...
    def set_google_identity(self, user_id: int, google_email: str | None, google_name: str | None) -> None: ...
    def ensure_admin(self, *, email: str, password_hash: str) -> User: ...


class GoogleTokensRepository(Protocol):
    def get_for_user(self, user_id: int) -> GoogleToken | None: ...
    def save_for_user(self, user_id: int, token: GoogleToken) -> None: ...


class DeepgramCredentialsRepository(Protocol):
    def get_encrypted_for_user(self, user_id: int) -> str | None: ...
    def save_for_user(self, user_id: int, api_key_encrypted: str) -> None: ...


class DriveSettingsRepository(Protocol):
    def get_for_user(self, user_id: int) -> DriveSettings | None: ...
    def save_for_user(self, user_id: int, settings: DriveSettings) -> None: ...


class TranscriptionJobsRepository(Protocol):
    # Minimal subset compatible with postgres-worker's JobRepository naming.
    def create_job(
        self,
        *,
        user_id: int,
        status: str = "pending",
        source_file_id: str | None = None,
        source_file_name: str | None = None,
    ) -> Job: ...
    def list_jobs_for_user(self, user_id: int, limit: int | None = None) -> list[Job]: ...
    def find_active_for_user(self, user_id: int) -> Job | None: ...


@dataclass(frozen=True)
class RepositoryBundle:
    users: UsersRepository
    google_tokens: GoogleTokensRepository
    deepgram_credentials: DeepgramCredentialsRepository
    drive_settings: DriveSettingsRepository
    jobs: TranscriptionJobsRepository


class RepositoryBackendUnavailable(RuntimeError):
    pass


def build_repositories(settings) -> RepositoryBundle:
    """Build the production Postgres-backed bundle provided by postgres-core.

    Integration point: postgres-core must expose
    ``app/db/postgres.py::build_repositories(database_url) -> RepositoryBundle``
    satisfying the Protocols above. Until then this raises a clear error.
    """
    try:
        from app.db.postgres import build_repositories as build_pg
    except ImportError as exc:  # postgres-core not integrated yet
        raise RepositoryBackendUnavailable(
            "Camada PostgreSQL (postgres-core) indisponível: integre a branch "
            "postgres-core (app.db.postgres.build_repositories) para rodar o app web."
        ) from exc
    return build_pg(settings.database_url)
