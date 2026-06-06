from __future__ import annotations

from dataclasses import dataclass

from app.core.models import JobStatus
from app.core.ports import Repositories
from app.exports import DEFAULT_FORMAT, Export, build_export, is_supported
from app.processor import sanitize_filename


class DownloadError(Exception):
    """Raised when a transcript cannot be served. `code` is a stable reason string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # not_found | not_completed | no_transcript | bad_format


@dataclass(frozen=True)
class DownloadableTranscript:
    filename: str
    text: str


def _resolve_completed_transcript(
    repositories: Repositories,
    job_id: int,
    requester_user_id: int,
    is_admin: bool,
):
    """Shared ownership + status gate. Returns ``(job, transcript)`` or raises."""
    job = repositories.jobs.get_job(job_id)
    if job is None or (job.user_id != requester_user_id and not is_admin):
        # Do not leak existence of other users' jobs.
        raise DownloadError("not_found", "Job not found")
    if job.status != JobStatus.COMPLETED.value:
        raise DownloadError("not_completed", "Job is not completed yet")
    transcript = repositories.transcripts.get_by_job(job_id)
    if transcript is None:
        raise DownloadError("no_transcript", "Transcript is not available")
    return job, transcript


def get_downloadable_transcript(
    repositories: Repositories,
    job_id: int,
    requester_user_id: int,
    is_admin: bool = False,
) -> DownloadableTranscript:
    job, transcript = _resolve_completed_transcript(
        repositories, job_id, requester_user_id, is_admin
    )
    base = sanitize_filename(job.source_file_name or f"job_{job_id}")
    return DownloadableTranscript(filename=f"{base}_Transcricao.txt", text=transcript.text)


def get_transcript_export(
    repositories: Repositories,
    job_id: int,
    requester_user_id: int,
    fmt: str = DEFAULT_FORMAT,
    is_admin: bool = False,
) -> Export:
    """Build a transcript export in ``fmt`` (txt|json|srt|vtt|md) for a completed job.

    Same per-user ownership and status rules as the TXT download. An unknown format
    raises ``DownloadError('bad_format', ...)`` so the route can answer 400.
    """
    if not is_supported(fmt):
        raise DownloadError("bad_format", f"Unsupported export format: {fmt}")
    job, transcript = _resolve_completed_transcript(
        repositories, job_id, requester_user_id, is_admin
    )
    base = sanitize_filename(job.source_file_name or f"job_{job_id}")
    return build_export(
        fmt,
        transcript_text=transcript.text,
        payload=transcript.json_payload,
        base_name=base,
        original_name=job.source_file_name or "",
        file_id=job.source_file_id or "",
    )
