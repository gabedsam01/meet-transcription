from datetime import datetime, timezone

import pytest

from app.repositories.memory import build_memory_repositories
from app.services.download_service import DownloadError, get_downloadable_transcript


def _now():
    return datetime.now(timezone.utc)


def _completed_job_with_transcript(repos, user_id=7, name="Weekly Sync.mp4"):
    job = repos.jobs.create_job(user_id, "src-1", name, _now())
    repos.jobs.claim_next_pending_job("w", _now())
    repos.transcripts.create(job.id, user_id, "transcript text", {"k": "v"}, None, _now())
    repos.jobs.mark_completed(job.id, _now())
    return job


def test_owner_downloads_sanitized_attachment():
    repos = build_memory_repositories()
    job = _completed_job_with_transcript(repos)
    result = get_downloadable_transcript(repos, job.id, requester_user_id=7)
    assert result.text == "transcript text"
    assert result.filename == "Weekly_Sync_Transcricao.txt"


def test_admin_downloads_other_users_job():
    repos = build_memory_repositories()
    job = _completed_job_with_transcript(repos, user_id=7)
    result = get_downloadable_transcript(repos, job.id, requester_user_id=99, is_admin=True)
    assert result.text == "transcript text"


def test_stranger_is_denied_as_not_found():
    repos = build_memory_repositories()
    job = _completed_job_with_transcript(repos, user_id=7)
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, job.id, requester_user_id=99)
    assert exc.value.code == "not_found"


def test_missing_job_is_not_found():
    repos = build_memory_repositories()
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, 12345, requester_user_id=7)
    assert exc.value.code == "not_found"


def test_not_completed_job_is_rejected():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())  # pending
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, job.id, requester_user_id=7)
    assert exc.value.code == "not_completed"


def test_completed_without_transcript_is_rejected():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.mark_completed(job.id, _now())  # completed but no transcript row
    with pytest.raises(DownloadError) as exc:
        get_downloadable_transcript(repos, job.id, requester_user_id=7)
    assert exc.value.code == "no_transcript"
