from __future__ import annotations

from dataclasses import dataclass

from app.core.models import JobStatus
from app.core.ports import Repositories
from app.processor import sanitize_filename


class DownloadError(Exception):
    """Raised when a transcript cannot be served. `code` is a stable reason string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # not_found | not_completed | no_transcript


@dataclass(frozen=True)
class DownloadableTranscript:
    filename: str
    text: str


def get_downloadable_transcript(
    repositories: Repositories,
    job_id: int,
    requester_user_id: int,
    is_admin: bool = False,
) -> DownloadableTranscript:
    job = repositories.jobs.get_job(job_id)
    if job is None or (job.user_id != requester_user_id and not is_admin):
        # Do not leak existence of other users' jobs.
        raise DownloadError("not_found", "Job not found")
    if job.status != JobStatus.COMPLETED.value:
        raise DownloadError("not_completed", "Job is not completed yet")
    transcript = repositories.transcripts.get_by_job(job_id)
    if transcript is None:
        raise DownloadError("no_transcript", "Transcript is not available")
    base = sanitize_filename(job.source_file_name or f"job_{job_id}")
    return DownloadableTranscript(filename=f"{base}_Transcricao.txt", text=transcript.text)
