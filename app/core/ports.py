from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.core.models import GoogleToken, Job, Settings, Transcript


@runtime_checkable
class JobRepository(Protocol):
    def claim_next_pending_job(self, worker_id: str, now: datetime) -> Job | None:
        """Atomically hand exactly one pending job to one worker.

        Contract (PostgreSQL adapter): inside a single transaction,
            SELECT ... WHERE status='pending' ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED LIMIT 1;
        then mark it 'processing', attempts = attempts + 1, set started_at/updated_at,
        COMMIT, and return it (or None when no pending job is available). Download and
        transcription happen OUTSIDE this transaction.
        """

    def claim_job(self, job_id: int, worker_id: str, now: datetime) -> Job | None:
        """Atomically claim a *specific* pending job by id (Redis-queue path).

        Contract (PostgreSQL adapter): inside a single transaction,
            SELECT ... WHERE id=:id AND status='pending'
            FOR UPDATE SKIP LOCKED;
        then mark it 'processing', attempts = attempts + 1, set started_at/updated_at,
        COMMIT, and return it. Return None when the job is missing or no longer
        pending — that None is the dedupe defense when the queue double-delivers an
        id. Download/transcription happen OUTSIDE this transaction.
        """

    def create_job(
        self, user_id: int, source_file_id: str | None,
        source_file_name: str | None, now: datetime,
    ) -> Job: ...

    def get_job(self, job_id: int) -> Job | None: ...

    def list_pending_jobs(self) -> list[Job]:
        """Return every 'pending' job, oldest first (created_at, id).

        Used by the Redis-queue reconciler to re-enqueue jobs that Postgres knows
        are pending but the queue may have lost. Postgres stays the source of truth.
        """

    def mark_completed(
        self, job_id: int, now: datetime, transcript_drive_file_id: str | None = None,
    ) -> None:
        """Mark a job completed.

        Atomicity contract: the worker's success path persists the transcript
        (TranscriptRepository.create) and then calls mark_completed. The PostgreSQL
        adapter SHOULD perform both writes in a single transaction so that a failure
        between them cannot leave a 'failed' job owning an orphan transcript. The
        failure is recoverable either way (the source file is simply reprocessed by a
        new job, since dedup only blocks pending/processing/completed), but atomic
        completion is preferred.
        """

    def mark_failed(self, job_id: int, error_message: str, now: datetime) -> None: ...

    def find_existing_job(
        self, user_id: int, source_file_id: str, statuses: tuple[str, ...],
    ) -> Job | None:
        """Return a job for (user_id, source_file_id) in any of `statuses`, else None.

        Used to avoid creating a duplicate when one is pending/processing/completed.
        """

    def reset_stale_processing_jobs(
        self, stale_before: datetime, now: datetime,
    ) -> list[Job]:
        """Mark every 'processing' job whose started_at/updated_at < stale_before as
        'failed' with a stale-timeout message; return the affected jobs."""

    def list_jobs_for_user(self, user_id: int) -> list[Job]: ...


@runtime_checkable
class TranscriptRepository(Protocol):
    def create(
        self, job_id: int, user_id: int, text: str,
        json_payload: dict[str, Any] | None, drive_file_id: str | None, now: datetime,
    ) -> Transcript: ...

    def get_by_job(self, job_id: int) -> Transcript | None: ...

    def search_transcripts(
        self, user_id: int, query: str, limit: int = 20,
    ) -> list[Transcript]:
        """User-scoped text search over ``transcript_text``, newest first.

        Always filters by ``user_id`` so a result can never leak another user's
        transcript. An empty/blank ``query`` returns ``[]``. The PostgreSQL adapter
        uses full-text search (``to_tsvector``/``plainto_tsquery``, backed by a GIN
        index); the in-memory fake does a case-insensitive substring match.
        """



@runtime_checkable
class SettingsRepository(Protocol):
    def get(self, user_id: int) -> Settings | None: ...


@runtime_checkable
class GoogleTokenRepository(Protocol):
    def get(self, user_id: int) -> GoogleToken | None: ...


@dataclass
class Repositories:
    jobs: JobRepository
    transcripts: TranscriptRepository
    settings: SettingsRepository
    google_tokens: GoogleTokenRepository
