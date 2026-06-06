from __future__ import annotations

import dataclasses
import threading
from datetime import timedelta

from app.core.models import (
    AutomationSettings,
    GoogleToken,
    Job,
    JobStatus,
    Settings,
    Transcript,
)
from app.core.ports import Repositories

_AUTOMATION_FIELDS = frozenset(
    {
        "auto_poll_enabled", "poll_interval_seconds", "max_files_per_poll",
        "last_poll_at", "last_success_at", "last_error_code", "last_error_message",
        "daily_jobs_limit", "max_file_size_mb", "monthly_cloud_minutes_limit",
        "max_file_duration_minutes",
    }
)


def _copy(obj):
    return dataclasses.replace(obj) if obj is not None else None


def _is_due(job, now) -> bool:
    """A pending job is claimable when it has no retry gate or the gate has passed."""
    return job.next_retry_at is None or job.next_retry_at <= now


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[int, Job] = {}
        self._seq = 0

    def create_job(self, user_id, source_file_id, source_file_name, now) -> Job:
        with self._lock:
            self._seq += 1
            job = Job(
                id=self._seq, user_id=user_id, status=JobStatus.PENDING.value,
                source_file_id=source_file_id, source_file_name=source_file_name,
                attempts=0, created_at=now, updated_at=now,
            )
            self._jobs[job.id] = job
            return _copy(job)

    def claim_next_pending_job(self, worker_id, now) -> Job | None:
        with self._lock:
            pending = sorted(
                (
                    j for j in self._jobs.values()
                    if j.status == JobStatus.PENDING.value and _is_due(j, now)
                ),
                key=lambda j: j.id,
            )
            if not pending:
                return None
            job = pending[0]
            job.status = JobStatus.PROCESSING.value
            job.attempts += 1
            job.started_at = now
            job.updated_at = now
            return _copy(job)

    def claim_job(self, job_id, worker_id, now) -> Job | None:
        """Atomically claim a *specific* pending job by id (Redis-queue path).

        Returns the now-processing job, or None when it is missing, no longer
        pending, or still in retry backoff. The None case is the dedupe defense:
        even if the queue hands the same id to two workers, only the first claim
        transitions it.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != JobStatus.PENDING.value:
                return None
            if not _is_due(job, now):
                return None
            job.status = JobStatus.PROCESSING.value
            job.attempts += 1
            job.started_at = now
            job.updated_at = now
            return _copy(job)

    def list_pending_jobs(self, now=None) -> list[Job]:
        with self._lock:
            pending = sorted(
                (
                    j for j in self._jobs.values()
                    if j.status == JobStatus.PENDING.value
                    and (now is None or _is_due(j, now))
                ),
                key=lambda j: j.id,
            )
            return [_copy(j) for j in pending]

    def get_job(self, job_id) -> Job | None:
        with self._lock:
            return _copy(self._jobs.get(job_id))

    def mark_completed(self, job_id, now, transcript_drive_file_id=None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.COMPLETED.value
            job.processed_at = now
            job.updated_at = now
            if transcript_drive_file_id is not None:
                job.transcript_drive_file_id = transcript_drive_file_id

    def mark_failed(self, job_id, error_message, now, error_code=None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.FAILED.value
            job.error_message = error_message
            job.last_error_code = error_code
            job.updated_at = now

    def schedule_retry(self, job_id, now, *, next_retry_at, error_code, error_message) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.PENDING.value
            job.next_retry_at = next_retry_at
            job.last_error_code = error_code
            job.error_message = error_message
            job.updated_at = now  # attempts and source_file_id are preserved

    def reset_job_for_retry(self, job_id, now) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.PENDING.value
            job.attempts = 0
            job.next_retry_at = None
            job.error_message = None
            job.last_error_code = None
            job.started_at = None
            job.processed_at = None
            job.updated_at = now

    def count_jobs_created_since(self, user_id, since) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.user_id == user_id and j.created_at is not None and j.created_at >= since
            )

    def count_jobs_by_status(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for j in self._jobs.values():
                counts[j.status] = counts.get(j.status, 0) + 1
            return counts

    def find_existing_job(self, user_id, source_file_id, statuses) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if (
                    job.user_id == user_id
                    and job.source_file_id == source_file_id
                    and job.status in statuses
                ):
                    return _copy(job)
            return None

    def reset_stale_processing_jobs(self, stale_before, now) -> list[Job]:
        with self._lock:
            reset: list[Job] = []
            for job in self._jobs.values():
                if job.status != JobStatus.PROCESSING.value:
                    continue
                marker = job.started_at or job.updated_at
                if marker is not None and marker < stale_before:
                    job.status = JobStatus.FAILED.value
                    job.error_message = "stale timeout: job exceeded processing window"
                    job.updated_at = now
                    reset.append(_copy(job))
            return reset

    def list_jobs_for_user(self, user_id) -> list[Job]:
        with self._lock:
            jobs = [_copy(j) for j in self._jobs.values() if j.user_id == user_id]
            jobs.sort(key=lambda j: j.id, reverse=True)
            return jobs


class InMemoryTranscriptRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_job: dict[int, Transcript] = {}
        self._seq = 0

    def create(self, job_id, user_id, text, json_payload, drive_file_id, now) -> Transcript:
        with self._lock:
            self._seq += 1
            transcript = Transcript(
                id=self._seq, job_id=job_id, user_id=user_id, text=text,
                json_payload=json_payload, drive_file_id=drive_file_id, created_at=now,
            )
            self._by_job[job_id] = transcript
            return _copy(transcript)

    def get_by_job(self, job_id) -> Transcript | None:
        with self._lock:
            return _copy(self._by_job.get(job_id))


class InMemorySettingsRepository:
    def __init__(self) -> None:
        self._settings: dict[int, Settings] = {}

    def set(self, settings: Settings) -> None:  # test/dev seeding helper
        self._settings[settings.user_id] = settings

    def get(self, user_id) -> Settings | None:
        return _copy(self._settings.get(user_id))


class InMemoryGoogleTokenRepository:
    def __init__(self) -> None:
        self._tokens: dict[int, GoogleToken] = {}

    def set(self, user_id: int, token: GoogleToken) -> None:  # test/dev seeding helper
        self._tokens[user_id] = token

    def get(self, user_id) -> GoogleToken | None:
        return _copy(self._tokens.get(user_id))


class InMemoryAutomationSettingsRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_user: dict[int, AutomationSettings] = {}

    def get_for_user(self, user_id) -> AutomationSettings | None:
        with self._lock:
            return _copy(self._by_user.get(user_id))

    def upsert_for_user(self, user_id, **fields) -> AutomationSettings:
        with self._lock:
            current = self._by_user.get(user_id) or AutomationSettings(user_id=user_id)
            for key, value in fields.items():
                if key in _AUTOMATION_FIELDS:
                    setattr(current, key, value)
            self._by_user[user_id] = current
            return _copy(current)

    def list_due_for_poll(self, now, limit) -> list[AutomationSettings]:
        with self._lock:
            due = [
                s for s in self._by_user.values()
                if s.auto_poll_enabled and _poll_due(s, now)
            ]
        # Oldest poll first (None counts as oldest) so users are served fairly.
        due.sort(key=lambda s: (s.last_poll_at is not None, s.last_poll_at or now))
        return [_copy(s) for s in due[:limit]]

    def mark_poll_result(self, user_id, now, *, success, error_code=None, error_message=None) -> None:
        with self._lock:
            current = self._by_user.get(user_id) or AutomationSettings(user_id=user_id)
            current.last_poll_at = now
            if success:
                current.last_success_at = now
                current.last_error_code = None
                current.last_error_message = None
            else:
                current.last_error_code = error_code
                current.last_error_message = error_message
            self._by_user[user_id] = current


def _poll_due(settings: AutomationSettings, now) -> bool:
    if settings.last_poll_at is None:
        return True
    return settings.last_poll_at <= now - timedelta(seconds=settings.poll_interval_seconds)


def build_memory_repositories() -> Repositories:
    return Repositories(
        jobs=InMemoryJobRepository(),
        transcripts=InMemoryTranscriptRepository(),
        settings=InMemorySettingsRepository(),
        google_tokens=InMemoryGoogleTokenRepository(),
        automation=InMemoryAutomationSettingsRepository(),
    )
