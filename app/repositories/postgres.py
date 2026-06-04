"""PostgreSQL repositories for the worker branch (``feat/postgres-worker``).

``build_postgres_repositories()`` returns a ``Repositories`` bundle whose members
satisfy the Protocols in that branch's ``app/core/ports.py``. Adapters over the
canonical ``app/database/`` layer; they return the worker's domain dataclasses
(timestamps as ``datetime``). Persistence only — no download/transcription.

At-rest encryption is this layer's responsibility. The worker receives
ready-to-use domain objects: ``GoogleTokenRepository.get`` and
``SettingsRepository.get`` decrypt the Google token and Deepgram key here, using
APP_SECRET_KEY (the same Fernet derivation the web layer used to encrypt them).
The worker never sees ciphertext and never decrypts. Secrets are never logged.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.database import models
from app.database.connection import (
    create_engine_from_url,
    get_database_url,
    normalize_database_url,
)
from app.database.repositories import (
    DeepgramCredentialRepository as CoreDeepgram,
    GoogleTokenRepository as CoreTokens,
    UserDriveSettingsRepository as CoreSettings,
)
from app.web.security import decrypt_value, fernet_from_secret

try:  # Prefer the real contract once the worker branch is merged.
    from app.core.models import (  # type: ignore
        GoogleToken,
        Job,
        Settings,
        Transcript,
    )
    from app.core.ports import Repositories  # type: ignore
except ImportError:  # postgres-core standalone
    from app.repositories._worker_contract import (
        GoogleToken,
        Job,
        Repositories,
        Settings,
        Transcript,
    )

_STALE_MESSAGE = "stale timeout: job exceeded processing window"


def _scopes_to_list(scopes: Any) -> list:
    """Worker-side canonical shape: a list (empty list for empty/absent scopes).

    The DB stores scopes as a JSONB list; the worker's credential builder accepts
    a list directly. Only the auth border degrades to an empty string.
    """
    if not scopes:
        return []
    if isinstance(scopes, (list, tuple)):
        return list(scopes)
    return str(scopes).split()


class CredentialDecryptionError(RuntimeError):
    """An encrypted credential is present but APP_SECRET_KEY is missing to decrypt it."""


class _Decryptor:
    """Turn at-rest ciphertext into plaintext for the worker's domain objects.

    The Fernet key is derived from APP_SECRET_KEY with the same derivation the
    web layer used to encrypt the values. The key is only required when there is
    actually something to decrypt; secrets are never logged.
    """

    def __init__(self, app_secret_key: str | None) -> None:
        self._key = app_secret_key
        self._fernet = None

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._fernet is None:
            if not (self._key or "").strip():
                raise CredentialDecryptionError(
                    "APP_SECRET_KEY is required to decrypt stored credentials "
                    "(Google token / Deepgram key) but is not set."
                )
            self._fernet = fernet_from_secret(self._key)
        return decrypt_value(self._fernet, value)


def _to_job(j: models.TranscriptionJob | None) -> Job | None:
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
        created_at=j.created_at,
        updated_at=j.updated_at,
        started_at=j.started_at,
        processed_at=j.processed_at,
    )


def _to_transcript(t: models.Transcript | None) -> Transcript | None:
    if t is None:
        return None
    return Transcript(
        id=t.id,
        job_id=t.job_id,
        user_id=t.user_id,
        text=t.transcript_text,
        json_payload=t.transcript_json,
        drive_file_id=t.drive_file_id,
        created_at=t.created_at,
    )


class _Bound:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory


class PgJobRepository(_Bound):
    def claim_next_pending_job(self, worker_id: str, now: datetime) -> Job | None:
        with self._sf.begin() as s:
            stmt = (
                select(models.TranscriptionJob)
                .where(models.TranscriptionJob.status == "pending")
                .order_by(models.TranscriptionJob.created_at, models.TranscriptionJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = s.scalar(stmt)
            if job is None:
                return None
            job.status = "processing"
            job.attempts = job.attempts + 1
            job.started_at = now
            job.updated_at = now
            s.flush()
            return _to_job(job)

    def create_job(
        self,
        user_id: int,
        source_file_id: str | None,
        source_file_name: str | None,
        now: datetime,
    ) -> Job:
        with self._sf.begin() as s:
            job = models.TranscriptionJob(
                user_id=user_id,
                status="pending",
                source_file_id=source_file_id,
                source_file_name=source_file_name,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            s.add(job)
            s.flush()
            return _to_job(job)

    def get_job(self, job_id: int) -> Job | None:
        with self._sf.begin() as s:
            return _to_job(s.get(models.TranscriptionJob, job_id))

    def mark_completed(
        self, job_id: int, now: datetime, transcript_drive_file_id: str | None = None
    ) -> None:
        with self._sf.begin() as s:
            job = s.get(models.TranscriptionJob, job_id)
            if job is None:
                return
            job.status = "completed"
            job.processed_at = now
            job.updated_at = now
            if transcript_drive_file_id is not None:
                job.transcript_drive_file_id = transcript_drive_file_id

    def mark_failed(self, job_id: int, error_message: str, now: datetime) -> None:
        with self._sf.begin() as s:
            job = s.get(models.TranscriptionJob, job_id)
            if job is None:
                return
            job.status = "failed"
            job.error_message = error_message
            job.updated_at = now

    def find_existing_job(
        self, user_id: int, source_file_id: str, statuses: tuple[str, ...]
    ) -> Job | None:
        with self._sf.begin() as s:
            stmt = (
                select(models.TranscriptionJob)
                .where(
                    models.TranscriptionJob.user_id == user_id,
                    models.TranscriptionJob.source_file_id == source_file_id,
                    models.TranscriptionJob.status.in_(statuses),
                )
                .order_by(models.TranscriptionJob.id.desc())
                .limit(1)
            )
            return _to_job(s.scalar(stmt))

    def reset_stale_processing_jobs(
        self, stale_before: datetime, now: datetime
    ) -> list[Job]:
        with self._sf.begin() as s:
            marker = func.coalesce(
                models.TranscriptionJob.started_at, models.TranscriptionJob.updated_at
            )
            stmt = select(models.TranscriptionJob).where(
                models.TranscriptionJob.status == "processing", marker < stale_before
            )
            reset: list[Job] = []
            for job in s.scalars(stmt):
                job.status = "failed"
                job.error_message = _STALE_MESSAGE
                job.updated_at = now
                reset.append(_to_job(job))
            return reset

    def list_jobs_for_user(self, user_id: int) -> list[Job]:
        with self._sf.begin() as s:
            stmt = (
                select(models.TranscriptionJob)
                .where(models.TranscriptionJob.user_id == user_id)
                .order_by(models.TranscriptionJob.id.desc())
            )
            return [_to_job(j) for j in s.scalars(stmt)]


class PgTranscriptRepository(_Bound):
    def create(
        self,
        job_id: int,
        user_id: int,
        text: str,
        json_payload: dict[str, Any] | None,
        drive_file_id: str | None,
        now: datetime,
    ) -> Transcript:
        with self._sf.begin() as s:
            transcript = models.Transcript(
                job_id=job_id,
                user_id=user_id,
                transcript_text=text,
                transcript_json=json_payload,
                drive_file_id=drive_file_id,
                created_at=now,
            )
            s.add(transcript)
            s.flush()
            return _to_transcript(transcript)

    def get_by_job(self, job_id: int) -> Transcript | None:
        with self._sf.begin() as s:
            stmt = select(models.Transcript).where(models.Transcript.job_id == job_id)
            return _to_transcript(s.scalar(stmt))


class PgSettingsRepository(_Bound):
    def __init__(self, session_factory: sessionmaker, decryptor: _Decryptor) -> None:
        super().__init__(session_factory)
        self._dec = decryptor

    def get(self, user_id: int) -> Settings | None:
        with self._sf.begin() as s:
            st = CoreSettings(s).get_for_user(user_id)
            if st is None:
                return None
            cred = CoreDeepgram(s).get_for_user(user_id)
            return Settings(
                user_id=user_id,
                source_drive_folder_id=st.source_drive_folder_id or "",
                destination_drive_folder_id=st.destination_drive_folder_id or "",
                save_copy_to_drive=st.save_copy_to_drive,
                deepgram_api_key=self._dec.decrypt(cred.encrypted_api_key) if cred else None,
            )


class PgGoogleTokenRepository(_Bound):
    def __init__(self, session_factory: sessionmaker, decryptor: _Decryptor) -> None:
        super().__init__(session_factory)
        self._dec = decryptor

    def get(self, user_id: int) -> GoogleToken | None:
        with self._sf.begin() as s:
            t = CoreTokens(s).get_for_user(user_id)
            if t is None:
                return None
            return GoogleToken(
                access_token=self._dec.decrypt(t.encrypted_access_token),
                token_uri=t.token_uri,
                client_id=t.client_id,
                refresh_token=self._dec.decrypt(t.encrypted_refresh_token),
                client_secret=self._dec.decrypt(t.client_secret),
                scopes=_scopes_to_list(t.scopes),
                expiry=t.expiry.isoformat() if t.expiry else None,
            )


def build_postgres_repositories(
    database_url: Any = None, *, engine=None, app_secret_key: str | None = None
) -> Repositories:
    """Build the worker's Postgres repository bundle.

    The worker factory calls this with no arguments, reading ``DATABASE_URL`` and
    ``APP_SECRET_KEY`` from the environment. ``APP_SECRET_KEY`` is used to decrypt
    stored credentials so the worker receives plaintext domain objects. Tests may
    pass a pre-built ``engine`` and an explicit ``app_secret_key``.
    """
    if engine is not None:
        eng = engine
    else:
        url = database_url if database_url is not None else get_database_url()
        eng = create_engine_from_url(normalize_database_url(url))
    factory = sessionmaker(
        bind=eng, autoflush=False, expire_on_commit=False, future=True
    )
    secret = app_secret_key if app_secret_key is not None else os.environ.get("APP_SECRET_KEY")
    decryptor = _Decryptor(secret)
    return Repositories(
        jobs=PgJobRepository(factory),
        transcripts=PgTranscriptRepository(factory),
        settings=PgSettingsRepository(factory, decryptor),
        google_tokens=PgGoogleTokenRepository(factory, decryptor),
    )
