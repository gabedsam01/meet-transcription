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


def test_claim_job_claims_a_specific_pending_job():
    repos = build_memory_repositories()
    first = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    second = repos.jobs.create_job(7, "src-2", "b.mp4", _now())

    claimed = repos.jobs.claim_job(second.id, "w1", _now())

    assert claimed.id == second.id
    assert claimed.status == JobStatus.PROCESSING.value
    assert claimed.attempts == 1
    assert claimed.started_at is not None
    # The other pending job is untouched (we claimed a specific id, not FIFO).
    assert repos.jobs.get_job(first.id).status == JobStatus.PENDING.value


def test_claim_job_is_one_shot_returns_none_when_not_pending():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    assert repos.jobs.claim_job(job.id, "w1", _now()) is not None
    # Second claim of the same (now processing) job is refused: dedupe defense.
    assert repos.jobs.claim_job(job.id, "w2", _now()) is None


def test_claim_job_returns_none_for_unknown_job():
    repos = build_memory_repositories()
    assert repos.jobs.claim_job(999, "w1", _now()) is None


def test_list_pending_jobs_is_fifo_and_only_pending():
    repos = build_memory_repositories()
    a = repos.jobs.create_job(7, "src-a", "a.mp4", _now())
    b = repos.jobs.create_job(8, "src-b", "b.mp4", _now())
    repos.jobs.create_job(9, "src-c", "c.mp4", _now())
    repos.jobs.claim_job(a.id, "w", _now())  # a -> processing, drops out of pending

    pending = repos.jobs.list_pending_jobs()

    assert [j.id for j in pending] == [b.id, b.id + 1]
    assert all(j.status == JobStatus.PENDING.value for j in pending)


def test_schedule_retry_returns_job_to_pending_keeping_attempts_and_source():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.claim_job(job.id, "w", _now())  # attempts -> 1, processing
    retry_at = _now() + timedelta(seconds=60)
    repos.jobs.schedule_retry(
        job.id, _now(), next_retry_at=retry_at,
        error_code="RATE_LIMIT", error_message="rate limited",
    )
    got = repos.jobs.get_job(job.id)
    assert got.status == JobStatus.PENDING.value
    assert got.attempts == 1  # preserved, not reset
    assert got.source_file_id == "src-1"  # never lost across a retry
    assert got.last_error_code == "RATE_LIMIT"
    assert got.error_message == "rate limited"
    assert got.next_retry_at == retry_at


def test_claim_skips_jobs_still_in_backoff_then_claims_when_due():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    future = _now() + timedelta(minutes=10)
    repos.jobs.schedule_retry(job.id, _now(), next_retry_at=future,
                              error_code="RATE_LIMIT", error_message="x")
    # Not yet due -> neither claim path hands it out.
    assert repos.jobs.claim_next_pending_job("w", _now()) is None
    assert repos.jobs.claim_job(job.id, "w", _now()) is None
    # Once now >= next_retry_at it is claimable again.
    later = future + timedelta(seconds=1)
    claimed = repos.jobs.claim_job(job.id, "w", later)
    assert claimed is not None and claimed.id == job.id


def test_list_pending_jobs_now_excludes_backoff_but_no_arg_returns_all():
    repos = build_memory_repositories()
    due = repos.jobs.create_job(7, "src-due", "d.mp4", _now())
    waiting = repos.jobs.create_job(7, "src-wait", "w.mp4", _now())
    repos.jobs.schedule_retry(waiting.id, _now(),
                              next_retry_at=_now() + timedelta(minutes=5),
                              error_code="RATE_LIMIT", error_message="x")
    gated = repos.jobs.list_pending_jobs(_now())
    assert [j.id for j in gated] == [due.id]
    # Back-compat: no `now` returns every pending job regardless of backoff.
    assert {j.id for j in repos.jobs.list_pending_jobs()} == {due.id, waiting.id}


def test_count_jobs_created_since_is_user_scoped():
    repos = build_memory_repositories()
    cutoff = _now()
    repos.jobs.create_job(7, "a", "a.mp4", cutoff + timedelta(seconds=1))
    repos.jobs.create_job(7, "b", "b.mp4", cutoff + timedelta(seconds=2))
    repos.jobs.create_job(8, "c", "c.mp4", cutoff + timedelta(seconds=3))
    repos.jobs.create_job(7, "old", "o.mp4", cutoff - timedelta(hours=1))
    assert repos.jobs.count_jobs_created_since(7, cutoff) == 2
    assert repos.jobs.count_jobs_created_since(8, cutoff) == 1


def test_mark_failed_stores_error_code():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.mark_failed(job.id, "bad key", _now(), error_code="KEY_INVALID")
    got = repos.jobs.get_job(job.id)
    assert got.status == JobStatus.FAILED.value
    assert got.last_error_code == "KEY_INVALID"
    assert got.error_message == "bad key"


def test_reset_job_for_retry_clears_state_back_to_pending():
    repos = build_memory_repositories()
    job = repos.jobs.create_job(7, "src-1", "a.mp4", _now())
    repos.jobs.claim_job(job.id, "w", _now())
    repos.jobs.mark_failed(job.id, "boom", _now(), error_code="UNEXPECTED")
    repos.jobs.reset_job_for_retry(job.id, _now())
    got = repos.jobs.get_job(job.id)
    assert got.status == JobStatus.PENDING.value
    assert got.attempts == 0
    assert got.next_retry_at is None
    assert got.error_message is None
    assert got.last_error_code is None
    assert got.source_file_id == "src-1"


def test_count_jobs_by_status():
    repos = build_memory_repositories()
    a = repos.jobs.create_job(7, "a", "a.mp4", _now())
    repos.jobs.create_job(7, "b", "b.mp4", _now())
    c = repos.jobs.create_job(7, "c", "c.mp4", _now())
    repos.jobs.mark_failed(a.id, "x", _now())
    repos.jobs.claim_job(c.id, "w", _now())
    counts = repos.jobs.count_jobs_by_status()
    assert counts.get("pending") == 1
    assert counts.get("failed") == 1
    assert counts.get("processing") == 1


def test_transcript_create_and_get_by_job():
    repos = build_memory_repositories()
    repos.transcripts.create(5, 7, "hello", {"k": "v"}, "drive-1", _now())
    got = repos.transcripts.get_by_job(5)
    assert got.text == "hello"
    assert got.json_payload == {"k": "v"}
    assert repos.transcripts.get_by_job(999) is None


def test_settings_and_token_seed_and_get():
    repos = build_memory_repositories()
    repos.settings.set(Settings(7, "src", "dst", True, "dg-key"))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))
    assert repos.settings.get(7).deepgram_api_key == "dg-key"
    assert repos.google_tokens.get(7).access_token == "a"
    assert repos.settings.get(999) is None
