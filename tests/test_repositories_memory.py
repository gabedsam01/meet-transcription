import threading
from datetime import datetime, timedelta, timezone

from app.core.models import GoogleToken, JobStatus, Settings
from app.repositories.memory import build_memory_repositories


def _now():
    return datetime.now(timezone.utc)


def test_create_job_is_pending_with_zero_attempts():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    assert job.status == JobStatus.PENDING.value
    assert job.attempts == 0
    assert job.source_file_id == "src-1"


def test_claim_marks_processing_increments_attempts_and_is_one_shot():
    repos = build_memory_repositories()
    repos.jobs.create_job(7, "src-1", "a.mp4", _now())

    claimed = repos.jobs.claim_next_pending_job("w1", _now())
    assert claimed.status == JobStatus.PROCESSING.value
    assert claimed.attempts == 1
    assert claimed.started_at is not None
    # No more pending jobs -> second claim returns None.
    assert repos.jobs.claim_next_pending_job("w2", _now()) is None


def test_claim_is_fifo_by_creation():
    repos = build_memory_repositories()
    first = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.create_job(7, "src-2", "b.mp4", _now())
    claimed = repos.jobs.claim_next_pending_job("w1", _now())
    assert claimed.id == first.id


def test_concurrent_claims_never_hand_out_the_same_job():
    repos = build_memory_repositories()
    for i in range(20):
        repos.jobs.create_job(7, f"src-{i}", f"{i}.mp4", _now())

    claimed_ids = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        while True:
            job = repos.jobs.claim_next_pending_job("w", _now())
            if job is None:
                return
            with lock:
                claimed_ids.append(job.id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_ids) == 20
    assert len(set(claimed_ids)) == 20  # no job claimed twice


def test_mark_completed_and_failed():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.mark_completed(job.id, _now(), transcript_drive_file_id="drive-1")
    done = repos.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert done.transcript_drive_file_id == "drive-1"
    assert done.processed_at is not None

    other = repos.jobs.create_job(7, "src-2", "b.mp4", _now())
    repos.jobs.mark_failed(other.id, "boom", _now())
    failed = repos.jobs.get_job(other.id)
    assert failed.status == JobStatus.FAILED.value
    assert failed.error_message == "boom"


def test_find_existing_job_filters_by_status():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    assert repos.jobs.find_existing_job(7, "src-1", ("pending",)).id == job.id
    assert repos.jobs.find_existing_job(7, "src-1", ("completed",)) is None
    assert repos.jobs.find_existing_job(99, "src-1", ("pending",)) is None


def test_reset_stale_processing_jobs():
    repos = build_memory_repositories()
    fresh = repos.jobs.create_job(7, "src-fresh", "f.mp4", _now())
    stale = repos.jobs.create_job(7, "src-stale", "s.mp4", _now())
    pending = repos.jobs.create_job(7, "src-pending", "p.mp4", _now())
    repos.jobs.claim_next_pending_job("w", _now())  # claims `fresh` (FIFO)
    repos.jobs.claim_next_pending_job("w", _now())  # claims `stale`
    old = _now() - timedelta(hours=3)
    repos.jobs._jobs[stale.id].started_at = old  # test reaches into the fake

    reset = repos.jobs.reset_stale_processing_jobs(_now() - timedelta(hours=1), _now())

    assert [j.id for j in reset] == [stale.id]
    assert repos.jobs.get_job(stale.id).status == JobStatus.FAILED.value
    assert "stale" in repos.jobs.get_job(stale.id).error_message
    assert repos.jobs.get_job(fresh.id).status == JobStatus.PROCESSING.value
    assert repos.jobs.get_job(pending.id).status == JobStatus.PENDING.value


def test_transcript_create_and_get_by_job():
    repos = build_memory_repositories()
    repos.transcripts.create(5, 7, "hello", {"k": "v"}, "drive-1", _now())
    got = repos.transcripts.get_by_job(5)
    assert got.text == "hello"
    assert got.json_payload == {"k": "v"}
    assert repos.transcripts.get_by_job(999) is None


def test_settings_and_token_seed_and_get():
    repos = build_memory_repositories()
    repos.settings.set(Settings(7, "src", "dst", 300, True, "dg-key"))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))
    assert repos.settings.get(7).deepgram_api_key == "dg-key"
    assert repos.google_tokens.get(7).access_token == "a"
    assert repos.settings.get(999) is None
