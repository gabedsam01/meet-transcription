"""Integration tests for the worker-branch bridge (app/repositories/postgres.py)."""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.db._auth_contract import DriveSettings, GoogleToken
from app.db.postgres import build_repositories as build_auth
from app.repositories.postgres import CredentialDecryptionError, build_postgres_repositories
from app.web.security import encrypt_value, fernet_from_secret

_SECRET = "a-long-secret-for-tests"


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


def _seed_encrypted_token(auth, uid, fernet):
    auth.google_tokens.save_for_user(
        uid,
        GoogleToken(
            access_token=encrypt_value(fernet, "PLAIN_at"),
            refresh_token=encrypt_value(fernet, "PLAIN_rt"),
            token_uri="uri",
            client_id="cid",
            client_secret=encrypt_value(fernet, "PLAIN_cs"),
            scopes="scope-a scope-b",
            expiry="2026-06-03T10:00:00Z",
        ),
    )


def test_settings_get_returns_decrypted_deepgram_key(pg):
    fernet = fernet_from_secret(_SECRET)
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
    auth.deepgram_credentials.save_for_user(uid, encrypt_value(fernet, "PLAIN_dg"))

    repos = build_postgres_repositories(engine=pg, app_secret_key=_SECRET)
    settings = repos.settings.get(uid)
    assert settings.user_id == uid
    assert settings.source_drive_folder_id == "sid"
    assert settings.destination_drive_folder_id == "did"
    assert settings.save_copy_to_drive is True
    assert settings.deepgram_api_key == "PLAIN_dg"  # decrypted, ready to use
    assert repos.settings.get(999999) is None


def test_google_token_get_returns_decrypted_token(pg):
    fernet = fernet_from_secret(_SECRET)
    auth = build_auth(engine=pg)
    uid = auth.users.create(email="gt@example.com", password_hash="h", role="user").id
    _seed_encrypted_token(auth, uid, fernet)

    repos = build_postgres_repositories(engine=pg, app_secret_key=_SECRET)
    tok = repos.google_tokens.get(uid)
    assert tok.access_token == "PLAIN_at"  # decrypted
    assert tok.refresh_token == "PLAIN_rt"  # decrypted
    assert tok.client_secret == "PLAIN_cs"  # decrypted
    assert tok.client_id == "cid"  # never encrypted
    assert isinstance(tok.scopes, str)
    assert tok.scopes == "scope-a scope-b"
    assert repos.google_tokens.get(999999) is None


def test_decryption_fails_clearly_without_app_secret_key(pg):
    fernet = fernet_from_secret(_SECRET)
    auth = build_auth(engine=pg)
    uid = auth.users.create(email="nokey@example.com", password_hash="h", role="user").id
    _seed_encrypted_token(auth, uid, fernet)
    auth.deepgram_credentials.save_for_user(uid, encrypt_value(fernet, "PLAIN_dg"))
    auth.drive_settings.save_for_user(
        uid,
        DriveSettings(
            source_drive_folder_url="su",
            source_drive_folder_id="sid",
            destination_drive_folder_url=None,
            destination_drive_folder_id="did",
            save_copy_to_drive=False,
        ),
    )

    nokey = build_postgres_repositories(engine=pg, app_secret_key=None)
    with pytest.raises(CredentialDecryptionError):
        nokey.google_tokens.get(uid)
    with pytest.raises(CredentialDecryptionError):
        nokey.settings.get(uid)


def test_settings_without_deepgram_credential_needs_no_key(pg):
    auth = build_auth(engine=pg)
    uid = auth.users.create(email="nodg@example.com", password_hash="h", role="user").id
    auth.drive_settings.save_for_user(
        uid,
        DriveSettings(
            source_drive_folder_url="su",
            source_drive_folder_id="sid",
            destination_drive_folder_url=None,
            destination_drive_folder_id="did",
            save_copy_to_drive=False,
        ),
    )

    # No Deepgram credential: nothing to decrypt, so no APP_SECRET_KEY is needed.
    settings = build_postgres_repositories(engine=pg, app_secret_key=None).settings.get(uid)
    assert settings.deepgram_api_key is None


def test_worker_adapter_does_not_log_secrets(pg, caplog):
    fernet = fernet_from_secret(_SECRET)
    auth = build_auth(engine=pg)
    uid = auth.users.create(email="log@example.com", password_hash="h", role="user").id
    auth.google_tokens.save_for_user(
        uid,
        GoogleToken(
            access_token=encrypt_value(fernet, "SECRET_AT"),
            refresh_token=encrypt_value(fernet, "SECRET_RT"),
            token_uri="uri",
            client_id="cid",
            client_secret=encrypt_value(fernet, "SECRET_CS"),
            scopes="s",
            expiry=None,
        ),
    )

    repos = build_postgres_repositories(engine=pg, app_secret_key=_SECRET)
    with caplog.at_level(logging.DEBUG):
        tok = repos.google_tokens.get(uid)

    assert tok.access_token == "SECRET_AT"
    for secret in ("SECRET_AT", "SECRET_RT", "SECRET_CS"):
        assert secret not in caplog.text
