"""Integration tests for the worker-branch bridge (app/repositories/postgres.py)."""

from datetime import datetime, timedelta, timezone

from app.db._auth_contract import DriveSettings, GoogleToken
from app.db.postgres import build_repositories as build_auth
from app.repositories.postgres import build_postgres_repositories


def _now() -> datetime:
    return datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)


def _seed_user(pg, email: str = "w@example.com") -> int:
    return build_auth(engine=pg).users.create(
        email=email, password_hash="h", role="user"
    ).id


def test_build_postgres_repositories_bundle(pg):
    repos = build_postgres_repositories(engine=pg)
    for attr in ("jobs", "transcripts", "settings", "google_tokens"):
        assert hasattr(repos, attr)


def test_claim_delivers_each_pending_job_exactly_once(pg):
    repos = build_postgres_repositories(engine=pg)
    uid = _seed_user(pg)
    now = _now()
    a = repos.jobs.create_job(uid, "src-a", "a.mp4", now)
    b = repos.jobs.create_job(uid, "src-b", "b.mp4", now)

    c1 = repos.jobs.claim_next_pending_job("w1", now)
    c2 = repos.jobs.claim_next_pending_job("w1", now)
    c3 = repos.jobs.claim_next_pending_job("w1", now)

    assert {c1.id, c2.id} == {a.id, b.id}
    assert c1.id != c2.id  # never the same job twice
    assert c3 is None
    assert c1.status == "processing"
    assert c1.started_at is not None
    assert c1.attempts == 1


def test_create_job_returns_worker_shape_with_datetimes(pg):
    repos = build_postgres_repositories(engine=pg)
    uid = _seed_user(pg)
    job = repos.jobs.create_job(uid, "s", "n.mp4", _now())
    assert job.status == "pending"
    assert isinstance(job.created_at, datetime)  # worker shape: datetimes
    assert job.started_at is None


def test_mark_completed_and_failed(pg):
    repos = build_postgres_repositories(engine=pg)
    uid = _seed_user(pg)
    now = _now()
    job = repos.jobs.create_job(uid, "s", "n.mp4", now)
    repos.jobs.mark_completed(job.id, now, transcript_drive_file_id="txt-1")
    done = repos.jobs.get_job(job.id)
    assert done.status == "completed"
    assert done.processed_at is not None
    assert done.transcript_drive_file_id == "txt-1"

    job2 = repos.jobs.create_job(uid, "s2", "n2.mp4", now)
    repos.jobs.mark_failed(job2.id, "kaboom", now)
    failed = repos.jobs.get_job(job2.id)
    assert failed.status == "failed"
    assert failed.error_message == "kaboom"


def test_find_existing_job_filters_by_status(pg):
    repos = build_postgres_repositories(engine=pg)
    uid = _seed_user(pg)
    now = _now()
    job = repos.jobs.create_job(uid, "src-x", "x.mp4", now)
    repos.jobs.mark_completed(job.id, now)

    assert repos.jobs.find_existing_job(uid, "src-x", ("completed",)).id == job.id
    assert repos.jobs.find_existing_job(uid, "src-x", ("pending", "processing")) is None
    assert repos.jobs.find_existing_job(uid, "nope", ("completed",)) is None


def test_reset_stale_processing_jobs(pg):
    repos = build_postgres_repositories(engine=pg)
    uid = _seed_user(pg)
    now = _now()
    job = repos.jobs.create_job(uid, "src", "n.mp4", now)
    # Claim with an old started_at so the job looks stale.
    repos.jobs.claim_next_pending_job("w1", now - timedelta(hours=2))

    reset = repos.jobs.reset_stale_processing_jobs(
        stale_before=now - timedelta(hours=1), now=now
    )
    assert [x.id for x in reset] == [job.id]
    assert repos.jobs.get_job(job.id).status == "failed"

    # A freshly-claimed job is not reset.
    repos.jobs.create_job(uid, "src2", "n2.mp4", now)
    repos.jobs.claim_next_pending_job("w1", now)
    assert repos.jobs.reset_stale_processing_jobs(
        stale_before=now - timedelta(hours=1), now=now
    ) == []


def test_list_jobs_for_user_newest_first(pg):
    repos = build_postgres_repositories(engine=pg)
    uid = _seed_user(pg)
    now = _now()
    a = repos.jobs.create_job(uid, "a", "a.mp4", now)
    b = repos.jobs.create_job(uid, "b", "b.mp4", now)
    assert [j.id for j in repos.jobs.list_jobs_for_user(uid)] == [b.id, a.id]


def test_transcript_create_and_get_by_job(pg):
    repos = build_postgres_repositories(engine=pg)
    uid = _seed_user(pg)
    now = _now()
    job = repos.jobs.create_job(uid, "s", "n.mp4", now)
    created = repos.transcripts.create(job.id, uid, "the text", {"k": 1}, "txt-9", now)
    assert created.text == "the text"
    assert created.json_payload == {"k": 1}
    assert created.drive_file_id == "txt-9"

    got = repos.transcripts.get_by_job(job.id)
    assert got.text == "the text"
    assert got.json_payload == {"k": 1}
    assert repos.transcripts.get_by_job(999999) is None


def test_settings_get_returns_worker_settings_shape(pg):
    auth = build_auth(engine=pg)
    uid = auth.users.create(email="s@example.com", password_hash="h", role="user").id
    auth.drive_settings.save_for_user(
        uid,
        DriveSettings(
            source_drive_folder_url="su",
            source_drive_folder_id="sid",
            destination_drive_folder_url="du",
            destination_drive_folder_id="did",
            save_copy_to_drive=True,
        ),
    )
    auth.deepgram_credentials.save_for_user(uid, "ENC")

    settings = build_postgres_repositories(engine=pg).settings.get(uid)
    assert settings.user_id == uid
    assert settings.source_drive_folder_id == "sid"
    assert settings.destination_drive_folder_id == "did"
    assert settings.save_copy_to_drive is True
    assert settings.deepgram_api_key == "ENC"  # ciphertext at the boundary
    assert build_postgres_repositories(engine=pg).settings.get(999999) is None


def test_google_token_get_returns_worker_token_shape(pg):
    auth = build_auth(engine=pg)
    uid = auth.users.create(email="gt@example.com", password_hash="h", role="user").id
    auth.google_tokens.save_for_user(
        uid,
        GoogleToken(
            access_token="CT",
            refresh_token="RT",
            token_uri="uri",
            client_id="cid",
            client_secret="CS",
            scopes="scope-a scope-b",
            expiry="2026-06-03T10:00:00Z",
        ),
    )

    tok = build_postgres_repositories(engine=pg).google_tokens.get(uid)
    assert tok.access_token == "CT"  # ciphertext at the boundary
    assert tok.client_id == "cid"
    assert isinstance(tok.scopes, str)
    assert tok.scopes == "scope-a scope-b"
    assert build_postgres_repositories(engine=pg).google_tokens.get(999999) is None
