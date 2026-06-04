from datetime import datetime, timezone

from app.core.models import GoogleToken, JobStatus, Settings
from app.worker.processor import JobProcessor
from tests.support import FakeDeepgramClient, FakeDriveClient, make_worker_container


def _now():
    return datetime.now(timezone.utc)


def _seed(repos, *, save_copy=False, deepgram_key="user-dg-key"):
    repos.settings.set(Settings(
        user_id=7, source_drive_folder_id="src", destination_drive_folder_id="dst",
        save_copy_to_drive=save_copy, deepgram_api_key=deepgram_key,
    ))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def _claim_one(repos, source_file_id="src-1", name="meeting.mp4"):
    repos.jobs.create_job(7, source_file_id, name, _now())
    return repos.jobs.claim_next_pending_job("w1", _now())


def test_process_completes_and_persists_transcript(tmp_path):
    drive = FakeDriveClient()
    deepgram = FakeDeepgramClient()
    container = make_worker_container(tmp_path, drive=drive, deepgram=deepgram)
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert "Ola mundo." in transcript.text
    assert transcript.json_payload == deepgram.response
    assert deepgram.api_key == "user-dg-key"   # per-user key used
    assert drive.downloaded == ["src-1"]


def test_process_uploads_to_drive_when_enabled(tmp_path):
    drive = FakeDriveClient(upload_result="drive-txt-9")
    container = make_worker_container(tmp_path, drive=drive)
    _seed(container.repositories, save_copy=True)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    assert drive.uploaded and drive.uploaded[0].endswith("_Transcricao.txt")
    done = container.repositories.jobs.get_job(job.id)
    assert done.transcript_drive_file_id == "drive-txt-9"
    assert container.repositories.transcripts.get_by_job(job.id).drive_file_id == "drive-txt-9"


def test_process_skips_drive_upload_when_disabled(tmp_path):
    drive = FakeDriveClient()
    container = make_worker_container(tmp_path, drive=drive)
    _seed(container.repositories, save_copy=False)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    assert drive.uploaded == []
    assert container.repositories.jobs.get_job(job.id).transcript_drive_file_id is None


def test_process_fails_when_no_per_user_deepgram_key(tmp_path):
    container = make_worker_container(tmp_path)
    _seed(container.repositories, deepgram_key=None)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "Deepgram" in done.error_message


def test_process_marks_failed_on_transcription_error(tmp_path):
    container = make_worker_container(
        tmp_path, deepgram=FakeDeepgramClient(fail=True)
    )
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "deepgram failed" in done.error_message
    assert container.repositories.transcripts.get_by_job(job.id) is None


def test_process_cleans_only_its_own_job_dir(tmp_path):
    container = make_worker_container(tmp_path)
    _seed(container.repositories)
    job = _claim_one(container.repositories)
    # A sibling job's workspace must survive.
    sibling = tmp_path / "jobs" / "999"
    sibling.mkdir(parents=True)
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")

    JobProcessor(container).process(job)

    assert not (tmp_path / "jobs" / str(job.id)).exists()
    assert (sibling / "keep.txt").exists()
