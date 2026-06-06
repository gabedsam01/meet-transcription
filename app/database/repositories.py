"""Repositories: thin, focused data-access objects over a SQLAlchemy session.

Each repository wraps a ``Session`` and exposes clean create/get/update/list
operations. Repositories never commit — the caller controls the transaction via
``get_db`` (FastAPI) or ``session_scope`` (workers/background tasks). They
``flush`` when an operation needs the database-assigned id or server defaults.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import (
    DeepgramCredential,
    GoogleToken,
    ProviderCredential,
    TranscriptionJob,
    Transcript,
    User,
    UserDriveSettings,
    UserExtensionToken,
    UserModelSettings,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _apply(obj: Any, fields: dict[str, Any], allowed: Iterable[str]) -> None:
    allowed_set = set(allowed)
    for key, value in fields.items():
        if key in allowed_set:
            setattr(obj, key, value)


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        email: str,
        name: str | None = None,
        role: str = "user",
        password_hash: str | None = None,
        is_active: bool = True,
    ) -> User:
        user = User(
            email=email,
            name=name,
            role=role,
            password_hash=password_hash,
            is_active=is_active,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def get(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def get_or_create(
        self, *, email: str, name: str | None = None, role: str = "user"
    ) -> User:
        user = self.get_by_email(email)
        if user is None:
            return self.create(email=email, name=name, role=role)
        if name is not None and user.name != name:
            user.name = name
            self.session.flush()
        return user

    def ensure_admin(self, *, email: str, name: str | None = None) -> User:
        """Create the admin user, or promote/reactivate an existing row. Idempotent."""
        user = self.get_by_email(email)
        if user is None:
            return self.create(email=email, name=name, role="admin")
        changed = False
        if user.role != "admin":
            user.role = "admin"
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if name is not None and user.name != name:
            user.name = name
            changed = True
        if changed:
            self.session.flush()
        return user

    def list(self) -> Sequence[User]:
        return self.session.scalars(select(User).order_by(User.id)).all()

    def update(self, user_id: int, **fields: Any) -> User | None:
        user = self.get(user_id)
        if user is None:
            return None
        _apply(
            user,
            fields,
            {"name", "password_hash", "role", "is_active", "google_email", "google_name"},
        )
        self.session.flush()
        return user


class GoogleTokenRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int) -> GoogleToken | None:
        return self.session.scalar(
            select(GoogleToken).where(GoogleToken.user_id == user_id)
        )

    def upsert_for_user(
        self,
        user_id: int,
        *,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None,
        token_uri: str,
        client_id: str | None,
        client_secret: str | None,
        scopes: Any | None,
        expiry: datetime | None,
    ) -> GoogleToken:
        token = self.get_for_user(user_id)
        if token is None:
            token = GoogleToken(user_id=user_id)
            self.session.add(token)
        token.encrypted_access_token = encrypted_access_token
        token.encrypted_refresh_token = encrypted_refresh_token
        token.token_uri = token_uri
        token.client_id = client_id
        token.client_secret = client_secret
        token.scopes = scopes
        token.expiry = expiry
        self.session.flush()
        return token


class DeepgramCredentialRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int) -> DeepgramCredential | None:
        return self.session.scalar(
            select(DeepgramCredential).where(DeepgramCredential.user_id == user_id)
        )

    def upsert_for_user(self, user_id: int, *, encrypted_api_key: str) -> DeepgramCredential:
        credential = self.get_for_user(user_id)
        if credential is None:
            credential = DeepgramCredential(user_id=user_id)
            self.session.add(credential)
        credential.encrypted_api_key = encrypted_api_key
        self.session.flush()
        return credential


class ProviderCredentialRepository:
    """Per-user, per-provider encrypted API keys.

    Reads transparently fall back to the legacy ``deepgram_credentials`` row when
    no ``provider_credentials`` row exists yet for ``provider='deepgram'`` — so
    keys saved before the Models tab keep working without a forced migration.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int, provider: str) -> ProviderCredential | None:
        return self.session.scalar(
            select(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider == provider,
            )
        )

    def get_encrypted(self, user_id: int, provider: str) -> str | None:
        row = self.get_for_user(user_id, provider)
        if row is not None:
            return row.encrypted_api_key
        if provider == "deepgram":
            legacy = self.session.scalar(
                select(DeepgramCredential).where(DeepgramCredential.user_id == user_id)
            )
            if legacy is not None:
                return legacy.encrypted_api_key
        return None

    def list_for_user(self, user_id: int) -> dict[str, str]:
        rows = self.session.scalars(
            select(ProviderCredential).where(ProviderCredential.user_id == user_id)
        ).all()
        creds = {row.provider: row.encrypted_api_key for row in rows}
        if "deepgram" not in creds:
            legacy = self.session.scalar(
                select(DeepgramCredential).where(DeepgramCredential.user_id == user_id)
            )
            if legacy is not None:
                creds["deepgram"] = legacy.encrypted_api_key
        return creds

    def upsert_for_user(
        self, user_id: int, provider: str, *, encrypted_api_key: str
    ) -> ProviderCredential:
        row = self.get_for_user(user_id, provider)
        if row is None:
            row = ProviderCredential(user_id=user_id, provider=provider)
            self.session.add(row)
        row.encrypted_api_key = encrypted_api_key
        self.session.flush()
        return row


class UserModelSettingsRepository:
    FIELDS = (
        "primary_provider",
        "primary_model",
        "fallback_enabled",
        "fallback_provider",
        "fallback_model",
        "local_engine",
        "local_model",
        "local_quantization",
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int) -> UserModelSettings | None:
        return self.session.scalar(
            select(UserModelSettings).where(UserModelSettings.user_id == user_id)
        )

    def upsert_for_user(self, user_id: int, **fields: Any) -> UserModelSettings:
        row = self.get_for_user(user_id)
        if row is None:
            row = UserModelSettings(user_id=user_id)
            self.session.add(row)
        _apply(row, fields, self.FIELDS)
        self.session.flush()
        return row


class UserDriveSettingsRepository:
    FIELDS = (
        "source_drive_folder_url",
        "source_drive_folder_id",
        "source_drive_folder_name",
        "destination_drive_folder_url",
        "destination_drive_folder_id",
        "destination_drive_folder_name",
        "save_copy_to_drive",
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int) -> UserDriveSettings | None:
        return self.session.scalar(
            select(UserDriveSettings).where(UserDriveSettings.user_id == user_id)
        )

    def upsert_for_user(self, user_id: int, **fields: Any) -> UserDriveSettings:
        settings = self.get_for_user(user_id)
        if settings is None:
            settings = UserDriveSettings(user_id=user_id)
            self.session.add(settings)
        _apply(settings, fields, self.FIELDS)
        self.session.flush()
        return settings


class TranscriptionJobRepository:
    UPDATABLE = (
        "source_file_id",
        "source_file_name",
        "transcript_drive_file_id",
        "status",
        "error_message",
        "attempts",
        "processed_at",
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        user_id: int,
        status: str = "pending",
        source_file_id: str | None = None,
        source_file_name: str | None = None,
    ) -> TranscriptionJob:
        job = TranscriptionJob(
            user_id=user_id,
            status=status,
            source_file_id=source_file_id,
            source_file_name=source_file_name,
            attempts=0,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: int) -> TranscriptionJob | None:
        return self.session.get(TranscriptionJob, job_id)

    def update(self, job_id: int, **fields: Any) -> TranscriptionJob | None:
        job = self.get(job_id)
        if job is None:
            return None
        _apply(job, fields, self.UPDATABLE)
        # Stamp processed_at automatically when a job first reaches completed.
        if fields.get("status") == "completed" and job.processed_at is None:
            job.processed_at = _utcnow()
        self.session.flush()
        return job

    def list_for_user(self, user_id: int) -> Sequence[TranscriptionJob]:
        return self.session.scalars(
            select(TranscriptionJob)
            .where(TranscriptionJob.user_id == user_id)
            .order_by(TranscriptionJob.created_at.desc(), TranscriptionJob.id.desc())
        ).all()

    def latest_for_user(self, user_id: int, limit: int = 5) -> Sequence[TranscriptionJob]:
        return self.session.scalars(
            select(TranscriptionJob)
            .where(TranscriptionJob.user_id == user_id)
            .order_by(TranscriptionJob.created_at.desc(), TranscriptionJob.id.desc())
            .limit(limit)
        ).all()

    def get_active_for_user(self, user_id: int) -> TranscriptionJob | None:
        """Most recent pending/processing job for the user, or None."""
        return self.session.scalar(
            select(TranscriptionJob)
            .where(
                TranscriptionJob.user_id == user_id,
                TranscriptionJob.status.in_(("pending", "processing")),
            )
            .order_by(TranscriptionJob.id.desc())
            .limit(1)
        )

    def has_completed_for_source(self, user_id: int, source_file_id: str) -> bool:
        """Whether the user already has a completed job for this source file.

        Backs the partial unique index ``(user_id, source_file_id) WHERE
        status = 'completed'`` with an application-level check, so callers can
        skip work before hitting an IntegrityError.
        """
        existing = self.session.scalar(
            select(TranscriptionJob.id).where(
                TranscriptionJob.user_id == user_id,
                TranscriptionJob.source_file_id == source_file_id,
                TranscriptionJob.status == "completed",
            )
        )
        return existing is not None


class TranscriptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        job_id: int,
        user_id: int,
        transcript_text: str,
        transcript_json: dict | None = None,
    ) -> Transcript:
        transcript = Transcript(
            job_id=job_id,
            user_id=user_id,
            transcript_text=transcript_text,
            transcript_json=transcript_json,
        )
        self.session.add(transcript)
        self.session.flush()
        return transcript

    def get(self, transcript_id: int) -> Transcript | None:
        return self.session.get(Transcript, transcript_id)

    def get_for_job(self, job_id: int) -> Transcript | None:
        return self.session.scalar(
            select(Transcript).where(Transcript.job_id == job_id)
        )

    def list_for_user(self, user_id: int) -> Sequence[Transcript]:
        return self.session.scalars(
            select(Transcript)
            .where(Transcript.user_id == user_id)
            .order_by(Transcript.id.desc())
        ).all()


class UserExtensionTokenRepository:
    """Per-user Chrome-extension upload tokens.

    Only ``token_hash`` crosses the repository boundary; the real plaintext is
    generated once in the web layer and never persisted. ``list_for_user``
    returns every token, including revoked ones, ordered newest-first — the UI
    renders a small status badge so the user knows which are still active.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        user_id: int,
        name: str,
        token_hash: str,
        token_prefix: str,
    ) -> UserExtensionToken:
        token = UserExtensionToken(
            user_id=user_id,
            name=name.strip() or "Token",
            token_hash=token_hash,
            token_prefix=token_prefix,
        )
        self.session.add(token)
        self.session.flush()
        return token

    def get(self, token_id: int) -> UserExtensionToken | None:
        return self.session.get(UserExtensionToken, token_id)

    def get_for_user(self, token_id: int, user_id: int) -> UserExtensionToken | None:
        """Owner-scoped lookup used by the revoke action."""
        return self.session.scalar(
            select(UserExtensionToken).where(
                UserExtensionToken.id == token_id,
                UserExtensionToken.user_id == user_id,
            )
        )

    def list_for_user(self, user_id: int) -> Sequence[UserExtensionToken]:
        return self.session.scalars(
            select(UserExtensionToken)
            .where(UserExtensionToken.user_id == user_id)
            .order_by(UserExtensionToken.created_at.desc(), UserExtensionToken.id.desc())
        ).all()

    def find_by_hash(self, token_hash: str) -> UserExtensionToken | None:
        """Lookup by hash. Returns active OR revoked rows — callers must check
        ``revoked_at`` themselves to keep the policy in one place (the web
        auth helper)."""
        return self.session.scalar(
            select(UserExtensionToken).where(UserExtensionToken.token_hash == token_hash)
        )

    def revoke(self, token_id: int, user_id: int, *, now: datetime | None = None) -> bool:
        """Soft-revoke a token. Returns ``True`` if a row was updated."""
        token = self.get_for_user(token_id, user_id)
        if token is None or token.revoked_at is not None:
            return False
        token.revoked_at = now or _utcnow()
        self.session.flush()
        return True

    def touch(self, token_id: int, *, now: datetime | None = None) -> None:
        """Stamp ``last_used_at`` on a successful auth — best effort, never fatal."""
        token = self.session.get(UserExtensionToken, token_id)
        if token is not None:
            token.last_used_at = now or _utcnow()
            self.session.flush()
