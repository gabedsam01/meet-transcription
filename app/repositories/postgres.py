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
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import sessionmaker

from app.database import models
from app.database.connection import (
    create_engine_from_url,
    get_database_url,
    normalize_database_url,
)
from app.database.repositories import (
    GoogleTokenRepository as CoreTokens,
    ProviderCredentialRepository as CoreProviderCreds,
    UserDriveSettingsRepository as CoreSettings,
    UserModelSettingsRepository as CoreModelSettings,
)
from app.transcription.provider_config import normalize_model_settings
from app.web.security import decrypt_value, fernet_from_secret

try:  # Prefer the real contract once the worker branch is merged.
    from app.core.models import (  # type: ignore
        AutomationSettings,
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
    AutomationSettings = None  # type: ignore

_AUTOMATION_FIELDS = (
    "auto_poll_enabled", "poll_interval_seconds", "max_files_per_poll",
    "last_poll_at", "last_success_at", "last_error_code", "last_error_message",
    "daily_jobs_limit", "max_file_size_mb", "monthly_cloud_minutes_limit",
    "max_file_duration_minutes",
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
        last_error_code=j.last_error_code,
        next_retry_at=j.next_retry_at,
        attempts=j.attempts,
        created_at=j.created_at,
        updated_at=j.updated_at,
        started_at=j.started_at,
        processed_at=j.processed_at,
    )


def _to_model_settings(row):
    if row is None:
        return None
    return normalize_model_settings(
        primary_provider=row.primary_provider,
        primary_model=row.primary_model,
        fallback_enabled=row.fallback_enabled,
        fallback_provider=row.fallback_provider,
        fallback_model=row.fallback_model,
        local_engine=row.local_engine,
        local_model=row.local_model,
        local_quantization=row.local_quantization,
    )


def _due_predicate(now: datetime):
    """A pending job is claimable when its retry gate is unset or has passed."""
    return or_(
        models.TranscriptionJob.next_retry_at.is_(None),
        models.TranscriptionJob.next_retry_at <= now,
    )


def _to_automation(s: "models.UserAutomationSettings | None"):
    if s is None:
        return None
    return AutomationSettings(
        user_id=s.user_id,
        auto_poll_enabled=s.auto_poll_enabled,
        poll_interval_seconds=s.poll_interval_seconds,
        max_files_per_poll=s.max_files_per_poll,
        last_poll_at=s.last_poll_at,
        last_success_at=s.last_success_at,
        last_error_code=s.last_error_code,
        last_error_message=s.last_error_message,
        daily_jobs_limit=s.daily_jobs_limit,
        max_file_size_mb=s.max_file_size_mb,
        monthly_cloud_minutes_limit=s.monthly_cloud_minutes_limit,
        max_file_duration_minutes=s.max_file_duration_minutes,
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
                .where(
                    models.TranscriptionJob.status == "pending",
                    _due_predicate(now),
                )
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

    def claim_job(self, job_id: int, worker_id: str, now: datetime) -> Job | None:
        with self._sf.begin() as s:
            stmt = (
                select(models.TranscriptionJob)
                .where(
                    models.TranscriptionJob.id == job_id,
                    models.TranscriptionJob.status == "pending",
                    _due_predicate(now),
                )
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

    def list_pending_jobs(self, now: datetime | None = None) -> list[Job]:
        with self._sf.begin() as s:
            conditions = [models.TranscriptionJob.status == "pending"]
            if now is not None:
                conditions.append(_due_predicate(now))
            stmt = (
                select(models.TranscriptionJob)
                .where(*conditions)
                .order_by(
                    models.TranscriptionJob.created_at, models.TranscriptionJob.id
                )
            )
            return [_to_job(j) for j in s.scalars(stmt)]

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

    def mark_failed(
        self, job_id: int, error_message: str, now: datetime,
        error_code: str | None = None,
    ) -> None:
        with self._sf.begin() as s:
            job = s.get(models.TranscriptionJob, job_id)
            if job is None:
                return
            job.status = "failed"
            job.error_message = error_message
            job.last_error_code = error_code
            job.updated_at = now

    def schedule_retry(
        self, job_id: int, now: datetime, *, next_retry_at: datetime,
        error_code: str | None, error_message: str | None,
    ) -> None:
        with self._sf.begin() as s:
            job = s.get(models.TranscriptionJob, job_id)
            if job is None:
                return
            # Back to pending for a later attempt; attempts/source_file_id untouched.
            job.status = "pending"
            job.next_retry_at = next_retry_at
            job.last_error_code = error_code
            job.error_message = error_message
            job.updated_at = now

    def reset_job_for_retry(self, job_id: int, now: datetime) -> None:
        with self._sf.begin() as s:
            job = s.get(models.TranscriptionJob, job_id)
            if job is None:
                return
            job.status = "pending"
            job.attempts = 0
            job.next_retry_at = None
            job.error_message = None
            job.last_error_code = None
            job.started_at = None
            job.processed_at = None
            job.updated_at = now

    def count_jobs_created_since(self, user_id: int, since: datetime) -> int:
        with self._sf.begin() as s:
            stmt = (
                select(func.count())
                .select_from(models.TranscriptionJob)
                .where(
                    models.TranscriptionJob.user_id == user_id,
                    models.TranscriptionJob.created_at >= since,
                )
            )
            return int(s.scalar(stmt) or 0)

    def count_jobs_by_status(self) -> dict[str, int]:
        with self._sf.begin() as s:
            stmt = select(
                models.TranscriptionJob.status, func.count()
            ).group_by(models.TranscriptionJob.status)
            return {status: int(count) for status, count in s.execute(stmt)}

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
            # One read covers every provider key (incl. legacy deepgram_credentials
            # via the repository's compatibility fallback). Decrypt to plaintext;
            # the worker never sees ciphertext and never logs a key.
            encrypted = CoreProviderCreds(s).list_for_user(user_id)
            credentials = {
                provider: self._dec.decrypt(value)
                for provider, value in encrypted.items()
            }
            ms_row = CoreModelSettings(s).get_for_user(user_id)
            model_settings = _to_model_settings(ms_row)
            return Settings(
                user_id=user_id,
                source_drive_folder_id=st.source_drive_folder_id or "",
                destination_drive_folder_id=st.destination_drive_folder_id or "",
                save_copy_to_drive=st.save_copy_to_drive,
                deepgram_api_key=credentials.get("deepgram"),
                model_settings=model_settings,
                provider_credentials=credentials,
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


class PgAutomationSettingsRepository(_Bound):
    def get_for_user(self, user_id: int):
        with self._sf.begin() as s:
            return _to_automation(self._row(s, user_id))

    def upsert_for_user(self, user_id: int, **fields):
        with self._sf.begin() as s:
            row = self._row(s, user_id)
            if row is None:
                row = models.UserAutomationSettings(user_id=user_id)
                s.add(row)
            for key, value in fields.items():
                if key in _AUTOMATION_FIELDS:
                    setattr(row, key, value)
            s.flush()
            return _to_automation(row)

    def list_due_for_poll(self, now: datetime, limit: int):
        with self._sf.begin() as s:
            stmt = (
                select(models.UserAutomationSettings)
                .where(models.UserAutomationSettings.auto_poll_enabled.is_(True))
                .order_by(
                    models.UserAutomationSettings.last_poll_at.asc().nulls_first(),
                )
            )
            due = []
            for row in s.scalars(stmt):
                if row.last_poll_at is None or row.last_poll_at <= now - timedelta(
                    seconds=row.poll_interval_seconds
                ):
                    due.append(_to_automation(row))
                if len(due) >= limit:
                    break
            return due

    def mark_poll_result(
        self, user_id: int, now: datetime, *, success: bool,
        error_code: str | None = None, error_message: str | None = None,
    ) -> None:
        with self._sf.begin() as s:
            row = self._row(s, user_id)
            if row is None:
                row = models.UserAutomationSettings(user_id=user_id)
                s.add(row)
            row.last_poll_at = now
            if success:
                row.last_success_at = now
                row.last_error_code = None
                row.last_error_message = None
            else:
                row.last_error_code = error_code
                row.last_error_message = error_message
            s.flush()

    def _row(self, s, user_id: int):
        stmt = select(models.UserAutomationSettings).where(
            models.UserAutomationSettings.user_id == user_id
        )
        return s.scalar(stmt)


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
        automation=PgAutomationSettingsRepository(factory),
    )
