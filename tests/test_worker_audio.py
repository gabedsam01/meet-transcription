import json
from datetime import datetime, timezone

from app.audio.config import AudioConfig
from app.core.models import GoogleToken, JobStatus, Settings
from app.worker.processor import JobProcessor
from tests.support import FakeDeepgramClient, FakeDriveClient, make_worker_container


def _now():
    return datetime.now(timezone.utc)


def _seed(repos):
    repos.settings.set(Settings(
        user_id=7, source_drive_folder_id="src", destination_drive_folder_id="dst",
        save_copy_to_drive=False, deepgram_api_key="user-dg-key",
    ))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def _claim_one(repos):
    repos.jobs.create_job(7, "src-1", "meeting.mp4", _now())
    return repos.jobs.claim_next_pending_job("w1", _now())


def _ffprobe_runner(streams):
    payload = json.dumps({"streams": streams, "format": {"duration": "12.0"}})

    def runner(cmd):
        class _R:
            returncode = 0
            stdout = payload
            stderr = ""
        return _R()

    return runner


def test_audio_preprocessing_fails_fast_on_no_audio_track(tmp_path):
    drive = FakeDriveClient()
    container = make_worker_container(
        tmp_path, drive=drive,
        audio_config=AudioConfig.from_env({"AUDIO_PREPROCESSING_ENABLED": "true"}),
        audio_runner=_ffprobe_runner([{"codec_type": "video"}]),
    )
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "áudio" in done.error_message  # NoAudioTrackError user_message
    # The download happened, but transcription never ran (no transcript persisted).
    assert drive.downloaded == ["src-1"]
    assert container.repositories.transcripts.get_by_job(job.id) is None


def test_audio_preprocessing_passes_through_with_audio_track(tmp_path):
    deepgram = FakeDeepgramClient()
    container = make_worker_container(
        tmp_path, deepgram=deepgram,
        audio_config=AudioConfig.from_env({"AUDIO_PREPROCESSING_ENABLED": "true"}),
        audio_runner=_ffprobe_runner(
            [{"codec_type": "audio", "sample_rate": "48000", "channels": "2",
              "codec_name": "opus"}]
        ),
    )
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert container.repositories.transcripts.get_by_job(job.id) is not None


def test_audio_preprocessing_disabled_by_default_skips_probe(tmp_path):
    # No audio_config -> disabled: the (raising) runner must never be called.
    def _boom(cmd):
        raise AssertionError("probe_audio must not run when preprocessing is disabled")

    container = make_worker_container(tmp_path, audio_runner=_boom)
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    assert container.repositories.jobs.get_job(job.id).status == JobStatus.COMPLETED.value
