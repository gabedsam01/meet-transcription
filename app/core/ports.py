from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.core.models import (
    AutomationSettings,
    GoogleToken,
    Job,
    Settings,
    Transcript,
)


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

    def list_pending_jobs(self, now: datetime | None = None) -> list[Job]:
        """Return every 'pending' job, oldest first (created_at, id).

        Used by the Redis-queue reconciler to re-enqueue jobs that Postgres knows
        are pending but the queue may have lost. Postgres stays the source of truth.
        When ``now`` is given, jobs whose ``next_retry_at`` is still in the future
        (in retry backoff) are excluded, so the reconciler never wakes a job early.
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

    def mark_failed(
        self, job_id: int, error_message: str, now: datetime,
        error_code: str | None = None,
    ) -> None:
        """Terminal failure: status -> 'failed', store message + optional code."""

    def schedule_retry(
        self, job_id: int, now: datetime, *, next_retry_at: datetime,
        error_code: str | None, error_message: str | None,
    ) -> None:
        """Transient failure: return the job to 'pending' for a later retry.

        Sets ``next_retry_at`` (backoff gate), ``last_error_code`` and
        ``error_message``; **keeps** ``attempts`` and ``source_file_id`` so the
        retry resumes the same work without losing its place in the attempt budget.
        """

    def reset_job_for_retry(self, job_id: int, now: datetime) -> None:
        """User-triggered dead-letter retry: a 'failed' job -> fresh 'pending'.

        Resets ``attempts`` to 0 and clears ``next_retry_at``/``error_message``/
        ``last_error_code`` so the job starts over from scratch.
        """

    def count_jobs_created_since(self, user_id: int, since: datetime) -> int:
        """Count this user's jobs created at/after ``since`` (daily-limit guardrail)."""

    def count_jobs_by_status(self) -> dict[str, int]:
        """Return ``{status: count}`` across all jobs (queue observability)."""

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


@runtime_checkable
class AutomationSettingsRepository(Protocol):
    def get_for_user(self, user_id: int) -> AutomationSettings | None: ...

    def upsert_for_user(self, user_id: int, **fields) -> AutomationSettings:
        """Create or update the user's automation settings; return the new state."""

    def list_due_for_poll(self, now: datetime, limit: int) -> list[AutomationSettings]:
        """Enabled users whose last poll is None or older than their interval.

        Capped at ``limit``, oldest poll first, so the auto-poll thread spreads
        work fairly across users within ``AUTO_POLL_MAX_USERS_PER_TICK``.
        """

    def mark_poll_result(
        self, user_id: int, now: datetime, *, success: bool,
        error_code: str | None = None, error_message: str | None = None,
    ) -> None:
        """Stamp ``last_poll_at`` (always); on success also ``last_success_at`` and
        clear the error; on failure record the friendly error code/message."""


@dataclass
class Repositories:
    jobs: JobRepository
    transcripts: TranscriptRepository
    settings: SettingsRepository
    google_tokens: GoogleTokenRepository
    # Optional so existing constructions (tests, legacy) keep working; the real
    # builders (memory + postgres) always populate it.
    automation: AutomationSettingsRepository | None = None
