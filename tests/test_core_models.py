from datetime import datetime, timezone

from app.core.models import GoogleToken, Job, JobStatus, Settings, Transcript


def test_job_status_values():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.PROCESSING.value == "processing"
    assert JobStatus.COMPLETED.value == "completed"
    assert JobStatus.FAILED.value == "failed"
    assert JobStatus.SKIPPED.value == "skipped"


def test_job_defaults():
    job = Job(id=1, user_id=7, status=JobStatus.PENDING.value)
    assert job.attempts == 0
    assert job.source_file_id is None
    assert job.transcript_drive_file_id is None


def test_settings_and_token_and_transcript_construct():
    settings = Settings(
        user_id=7, source_drive_folder_id="src", destination_drive_folder_id="dst",
        poll_interval_seconds=300, save_copy_to_drive=True, deepgram_api_key="dg",
    )
    token = GoogleToken(access_token="a", token_uri="u", client_id="c")
    now = datetime.now(timezone.utc)
    transcript = Transcript(
        id=1, job_id=2, user_id=7, text="hello", json_payload={"k": "v"},
        drive_file_id="d", created_at=now,
    )
    assert settings.save_copy_to_drive is True
    assert token.refresh_token is None
    assert transcript.json_payload == {"k": "v"}
