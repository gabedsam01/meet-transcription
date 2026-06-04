from __future__ import annotations

import dataclasses
import threading
from app.core.models import GoogleToken, Job, JobStatus, Settings, Transcript
from app.core.ports import Repositories


def _copy(obj):
    return dataclasses.replace(obj) if obj is not None else None


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
                (j for j in self._jobs.values() if j.status == JobStatus.PENDING.value),
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

    def mark_failed(self, job_id, error_message, now) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.FAILED.value
            job.error_message = error_message
            job.updated_at = now

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


def build_memory_repositories() -> Repositories:
    return Repositories(
        jobs=InMemoryJobRepository(),
        transcripts=InMemoryTranscriptRepository(),
        settings=InMemorySettingsRepository(),
        google_tokens=InMemoryGoogleTokenRepository(),
    )
