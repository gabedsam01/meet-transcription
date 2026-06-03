"""PostgreSQL repository bundle for the auth branch (``feat/auth-users-settings``).

``build_repositories(database_url) -> RepositoryBundle`` returns repositories
satisfying the Protocols in that branch's ``app/web/repositories.py``. The
repositories are thin adapters over the canonical ``app/database/`` layer: they
manage their own short-lived sessions and return frozen dataclasses (never ORM
objects). Encryption stays in the web layer — sensitive fields cross this
boundary as ciphertext.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.database.connection import create_engine_from_url, normalize_database_url
from app.database.repositories import (
    DeepgramCredentialRepository as CoreDeepgram,
    GoogleTokenRepository as CoreTokens,
    TranscriptionJobRepository as CoreJobs,
    UserDriveSettingsRepository as CoreSettings,
    UserRepository as CoreUsers,
)

try:  # Prefer the real contract once the auth branch is merged.
    from app.web.repositories import (  # type: ignore
        DriveSettings,
        GoogleToken,
        Job,
        RepositoryBundle,
        User,
    )
except ImportError:  # postgres-core standalone
    from app.db._auth_contract import (
        DriveSettings,
        GoogleToken,
        Job,
        RepositoryBundle,
        User,
    )


# --- mapping helpers --------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _scopes_to_str(scopes: Any) -> str:
    if scopes is None:
        return ""
    if isinstance(scopes, (list, tuple)):
        return " ".join(scopes)
    return str(scopes)


def _scopes_to_list(scopes: Any) -> list | None:
    if scopes is None:
        return None
    if isinstance(scopes, str):
        return scopes.split()
    return list(scopes)


def _parse_expiry(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _to_user(u) -> User | None:
    if u is None:
        return None
    return User(
        id=u.id,
        email=u.email,
        name=u.name,
        role=u.role,
        is_active=u.is_active,
        google_email=u.google_email,
        google_name=u.google_name,
    )


def _to_job(j) -> Job | None:
    if j is None:
        return None
    return Job(
        id=j.id,
        user_id=j.user_id,
        status=j.status,
        source_file_id=j.source_file_id,
        source_file_name=j.source_file_name,
        transcript_drive_file_id=j.transcript_drive_file_id,
        error_message=j.error_message,
        attempts=j.attempts,
        created_at=_iso(j.created_at),
        updated_at=_iso(j.updated_at),
        processed_at=_iso(j.processed_at),
    )


# --- repositories -----------------------------------------------------------


class _Bound:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory


class PgUsersRepository(_Bound):
    def get_by_email(self, email: str) -> User | None:
        with self._sf.begin() as s:
            return _to_user(CoreUsers(s).get_by_email(email))

    def get_by_id(self, user_id: int) -> User | None:
        with self._sf.begin() as s:
            return _to_user(CoreUsers(s).get(user_id))

    def get_password_hash(self, user_id: int) -> str | None:
        with self._sf.begin() as s:
            u = CoreUsers(s).get(user_id)
            return u.password_hash if u else None

    def list_all(self) -> list[User]:
        with self._sf.begin() as s:
            return [_to_user(u) for u in CoreUsers(s).list()]

    def create(self, *, email: str, password_hash: str, role: str, name: str | None = None) -> User:
        with self._sf.begin() as s:
            u = CoreUsers(s).create(
                email=email, password_hash=password_hash, role=role, name=name
            )
            return _to_user(u)

    def set_active(self, user_id: int, active: bool) -> None:
        with self._sf.begin() as s:
            CoreUsers(s).update(user_id, is_active=active)

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        with self._sf.begin() as s:
            CoreUsers(s).update(user_id, password_hash=password_hash)

    def set_google_identity(
        self, user_id: int, google_email: str | None, google_name: str | None
    ) -> None:
        with self._sf.begin() as s:
            CoreUsers(s).update(
                user_id, google_email=google_email, google_name=google_name
            )

    def ensure_admin(self, *, email: str, password_hash: str) -> User:
        with self._sf.begin() as s:
            repo = CoreUsers(s)
            u = repo.get_by_email(email)
            if u is None:
                u = repo.create(email=email, password_hash=password_hash, role="admin")
            else:
                if u.role != "admin":
                    u.role = "admin"
                if not u.is_active:
                    u.is_active = True
                s.flush()
            return _to_user(u)


class PgGoogleTokensRepository(_Bound):
    def get_for_user(self, user_id: int) -> GoogleToken | None:
        with self._sf.begin() as s:
            t = CoreTokens(s).get_for_user(user_id)
            if t is None:
                return None
            return GoogleToken(
                access_token=t.encrypted_access_token,
                refresh_token=t.encrypted_refresh_token,
                token_uri=t.token_uri,
                client_id=t.client_id,
                client_secret=t.client_secret,
                scopes=_scopes_to_str(t.scopes),
                expiry=_iso(t.expiry),
            )

    def save_for_user(self, user_id: int, token: GoogleToken) -> None:
        with self._sf.begin() as s:
            CoreTokens(s).upsert_for_user(
                user_id,
                encrypted_access_token=token.access_token,
                encrypted_refresh_token=token.refresh_token,
                token_uri=token.token_uri,
                client_id=token.client_id,
                client_secret=token.client_secret,
                scopes=_scopes_to_list(token.scopes),
                expiry=_parse_expiry(token.expiry),
            )


class PgDeepgramCredentialsRepository(_Bound):
    def get_encrypted_for_user(self, user_id: int) -> str | None:
        with self._sf.begin() as s:
            c = CoreDeepgram(s).get_for_user(user_id)
            return c.encrypted_api_key if c else None

    def save_for_user(self, user_id: int, api_key_encrypted: str) -> None:
        with self._sf.begin() as s:
            CoreDeepgram(s).upsert_for_user(user_id, encrypted_api_key=api_key_encrypted)


class PgDriveSettingsRepository(_Bound):
    def get_for_user(self, user_id: int) -> DriveSettings | None:
        with self._sf.begin() as s:
            st = CoreSettings(s).get_for_user(user_id)
            if st is None:
                return None
            return DriveSettings(
                source_drive_folder_url=st.source_drive_folder_url,
                source_drive_folder_id=st.source_drive_folder_id,
                destination_drive_folder_url=st.destination_drive_folder_url,
                destination_drive_folder_id=st.destination_drive_folder_id,
                save_copy_to_drive=st.save_copy_to_drive,
            )

    def save_for_user(self, user_id: int, settings: DriveSettings) -> None:
        with self._sf.begin() as s:
            CoreSettings(s).upsert_for_user(
                user_id,
                source_drive_folder_url=settings.source_drive_folder_url,
                source_drive_folder_id=settings.source_drive_folder_id,
                destination_drive_folder_url=settings.destination_drive_folder_url,
                destination_drive_folder_id=settings.destination_drive_folder_id,
                save_copy_to_drive=settings.save_copy_to_drive,
            )


class PgTranscriptionJobsRepository(_Bound):
    def create_job(
        self,
        *,
        user_id: int,
        status: str = "pending",
        source_file_id: str | None = None,
        source_file_name: str | None = None,
    ) -> Job:
        with self._sf.begin() as s:
            j = CoreJobs(s).create(
                user_id=user_id,
                status=status,
                source_file_id=source_file_id,
                source_file_name=source_file_name,
            )
            return _to_job(j)

    def list_jobs_for_user(self, user_id: int, limit: int | None = None) -> list[Job]:
        with self._sf.begin() as s:
            repo = CoreJobs(s)
            jobs = repo.latest_for_user(user_id, limit) if limit else repo.list_for_user(user_id)
            return [_to_job(j) for j in jobs]

    def find_active_for_user(self, user_id: int) -> Job | None:
        with self._sf.begin() as s:
            return _to_job(CoreJobs(s).get_active_for_user(user_id))


def build_repositories(database_url: Any = None, *, engine=None) -> RepositoryBundle:
    """Build the Postgres-backed RepositoryBundle the auth branch consumes.

    Pass ``database_url`` in production; tests may pass a pre-built ``engine``.
    """
    eng = engine if engine is not None else create_engine_from_url(
        normalize_database_url(database_url)
    )
    factory = sessionmaker(
        bind=eng, autoflush=False, expire_on_commit=False, future=True
    )
    return RepositoryBundle(
        users=PgUsersRepository(factory),
        google_tokens=PgGoogleTokensRepository(factory),
        deepgram_credentials=PgDeepgramCredentialsRepository(factory),
        drive_settings=PgDriveSettingsRepository(factory),
        jobs=PgTranscriptionJobsRepository(factory),
    )
