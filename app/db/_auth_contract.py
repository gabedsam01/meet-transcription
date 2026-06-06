"""Vendored copy of the auth branch's repository contract dataclasses.

Used only as a fallback when the real ``app.web.repositories`` module (owned by
``feat/auth-users-settings``) is not present on this branch. After that branch
is merged, ``app/db/postgres.py`` imports the real types instead, so these stay
structurally identical to ``feat/auth-users-settings``' ``app/web/repositories``.
"""

from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class ExtensionToken:
    id: int
    user_id: int
    name: str
    masked: str
    created_at: str | None
    last_used_at: str | None
    revoked_at: str | None


@dataclass(frozen=True)
class RepositoryBundle:
    users: object
    google_tokens: object
    deepgram_credentials: object
    drive_settings: object
    jobs: object
    provider_credentials: object | None = None
    model_settings: object | None = None
    extension_tokens: object | None = None
