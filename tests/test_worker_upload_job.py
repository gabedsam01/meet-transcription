from datetime import datetime, timezone

from app.core.models import JobStatus
from app.recordings import (
    RecordingMetadata,
    new_recording_id,
    recording_path,
    resolve_recording_file,
    source_file_id_for,
    write_metadata,
)
from app.transcription.config import TranscriptionConfig
from app.transcription.local_validation import ValidationProbes
from app.transcription.provider import TranscriptionResult
from app.worker.processor import JobProcessor
from tests.support import FakeDriveClient, make_worker_container


def _local_cfg(**over):
    env = {"LOCAL_TRANSCRIPTION_ENABLED": "true", "LOCAL_TRANSCRIPTION_ENGINE": "faster-whisper"}
    env.update(over)
    return TranscriptionConfig.from_env(env)


def _probes(valid):
    return ValidationProbes(
        module_available=lambda name: valid,
        path_exists=lambda p: True,
        is_executable=lambda p: True,
    )


class FakeLocalProvider:
    def __init__(self):
        self.calls = []

    def transcribe(self, source_path, *, original_name, file_id):
        self.calls.append((str(source_path), original_name, file_id))
        return TranscriptionResult(
            text="LOCAL TXT Olá",
            payload={
                "provider": "local", "engine": "faster-whisper", "model": "small",
                "language": "pt", "text": "Olá",
                "segments": [], "words": [], "utterances": [], "raw": {},
            },
        )


def _now():
    return datetime.now(timezone.utc)


def _seed_recording(recdir, *, content=b"webm-bytes"):
    recdir.mkdir(parents=True, exist_ok=True)
    rid = new_recording_id()
    media = recording_path(recdir, rid, ".webm")
    media.write_bytes(content)
    write_metadata(recdir, RecordingMetadata(
        recording_id=rid, filename=media.name, meeting_title="Weekly",
    ))
    return rid


def _claim_upload_job(repos, source_file_id, name="Weekly"):
    repos.jobs.create_job(7, source_file_id, name, _now())
    return repos.jobs.claim_next_pending_job("w1", _now())


def test_upload_job_transcribes_local_file_without_drive_or_token(tmp_path):
    recdir = tmp_path / "recordings"
    rid = _seed_recording(recdir)
    local = FakeLocalProvider()
    drive = FakeDriveClient()
    container = make_worker_container(
        tmp_path, drive=drive,
        transcription_config=_local_cfg(), transcription_probes=_probes(valid=True),
        build_local_provider=lambda cfg: local, recordings_dir=recdir,
    )
    # NOTE: no settings, no google token seeded — an upload job needs neither.
    job = _claim_upload_job(container.repositories, source_file_id_for(rid))

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert drive.downloaded == []  # never touched Drive
    assert local.calls, "local provider was invoked on the uploaded media"
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert transcript.text == "LOCAL TXT Olá"
    assert transcript.json_payload["provider"] == "local"
    # The recording (and its sidecar) is cleaned up once the job is terminal.
    assert resolve_recording_file(recdir, rid) is None


def test_upload_job_fails_friendly_when_recording_missing(tmp_path):
    recdir = tmp_path / "recordings"
    recdir.mkdir(parents=True, exist_ok=True)
    local = FakeLocalProvider()
    container = make_worker_container(
        tmp_path,
        transcription_config=_local_cfg(), transcription_probes=_probes(valid=True),
        build_local_provider=lambda cfg: local, recordings_dir=recdir,
    )
    # Job points at a recording that was never written to disk.
    job = _claim_upload_job(container.repositories, source_file_id_for(new_recording_id()))

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "Gravação" in done.error_message
    assert local.calls == []  # never reached transcription
