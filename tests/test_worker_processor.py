from datetime import datetime, timezone

from app.core.models import GoogleToken, JobStatus, Settings
from app.errors import DeepgramRateLimitError, ProviderKeyInvalidError
from app.queue.memory_queue import InMemoryTranscriptionQueue
from app.transcription.config import TranscriptionConfig
from app.transcription.local_validation import ValidationProbes
from app.transcription.provider import TranscriptionResult
from app.transcription.provider_config import normalize_model_settings
from app.worker.processor import JobProcessor, _backoff
from tests.support import (
    FakeCloudProvider,
    FakeDeepgramClient,
    FakeDriveClient,
    make_worker_container,
)


class RaisingDeepgram:
    def __init__(self, exc):
        self.exc = exc
        self.api_key = None

    def transcribe(self, video_path, api_key=None):
        raise self.exc


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
                "provider": "local",
                "engine": "faster-whisper",
                "model": "small",
                "language": "pt",
                "text": "Olá",
                "segments": [],
                "words": [],
                "utterances": [],
                "raw": {},
            },
        )


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
    # json_payload is now the normalized schema; the raw Deepgram response is kept
    # verbatim under "raw".
    assert transcript.json_payload["provider"] == "deepgram"
    assert transcript.json_payload["raw"] == deepgram.response
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


def test_process_reschedules_a_transient_error_for_retry(tmp_path):
    # An unknown/transient error is retried (not failed) until the attempt cap, so a
    # blip never permanently fails a job. attempts==1 here (< max) -> reschedule.
    container = make_worker_container(
        tmp_path, deepgram=FakeDeepgramClient(fail=True)
    )
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.PENDING.value          # back to pending for retry
    assert done.next_retry_at is not None                  # with a backoff gate
    assert done.last_error_code == "UNEXPECTED"
    assert done.attempts == 1                              # preserved
    assert done.source_file_id == "src-1"                  # never lost on retry
    assert container.repositories.transcripts.get_by_job(job.id) is None


def test_process_uses_local_provider_when_valid_without_deepgram_key(tmp_path):
    local = FakeLocalProvider()
    drive = FakeDriveClient()
    deepgram = FakeDeepgramClient()
    container = make_worker_container(
        tmp_path, drive=drive, deepgram=deepgram,
        transcription_config=_local_cfg(), transcription_probes=_probes(valid=True),
        build_local_provider=lambda cfg: local,
    )
    _seed(container.repositories, deepgram_key=None)  # local valid -> no key needed
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert local.calls and drive.downloaded == ["src-1"]
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert transcript.text == "LOCAL TXT Olá"
    assert transcript.json_payload["provider"] == "local"
    assert deepgram.api_key is None  # Deepgram was never built/called


def test_process_fails_clearly_when_local_invalid_and_no_deepgram_key(tmp_path):
    container = make_worker_container(
        tmp_path,
        transcription_config=_local_cfg(LOCAL_TRANSCRIPTION_DOC_URL="https://docs/local"),
        transcription_probes=_probes(valid=False),  # faster-whisper "not installed"
    )
    _seed(container.repositories, deepgram_key=None)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "Deepgram" in done.error_message
    assert "https://docs/local" in done.error_message


def test_process_falls_back_to_deepgram_when_local_invalid_with_key(tmp_path):
    deepgram = FakeDeepgramClient()
    container = make_worker_container(
        tmp_path, deepgram=deepgram,
        transcription_config=_local_cfg(), transcription_probes=_probes(valid=False),
    )
    _seed(container.repositories, deepgram_key="user-dg-key")
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert transcript.json_payload["provider"] == "deepgram"
    assert deepgram.api_key == "user-dg-key"


def _seed_models(repos, *, ms, credentials, deepgram_key=None):
    repos.settings.set(Settings(
        user_id=7, source_drive_folder_id="src", destination_drive_folder_id="dst",
        save_copy_to_drive=False, deepgram_api_key=deepgram_key,
        model_settings=ms, provider_credentials=dict(credentials),
    ))
    repos.google_tokens.set(7, GoogleToken(access_token="a", token_uri="u", client_id="c"))


def _with_max_attempts(container, n):
    import dataclasses
    return dataclasses.replace(container, settings=dataclasses.replace(container.settings, job_max_attempts=n))


def test_run_once_uses_configured_cloud_provider(tmp_path):
    # Requirement 14: the worker honours the user's Models-tab selection.
    cloud = FakeCloudProvider(provider_id="openrouter")
    drive = FakeDriveClient()
    deepgram = FakeDeepgramClient()
    container = make_worker_container(
        tmp_path, drive=drive, deepgram=deepgram, build_cloud_provider=cloud.builder
    )
    ms = normalize_model_settings(
        primary_provider="openrouter", primary_model="openai/whisper-large-v3"
    )
    _seed_models(container.repositories, ms=ms, credentials={"openrouter": "or-key"})
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert cloud.built == [("openrouter", "or-key", "openai/whisper-large-v3")]
    assert cloud.calls and drive.downloaded == ["src-1"]
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert transcript.json_payload["provider"] == "openrouter"
    assert deepgram.api_key is None  # Deepgram never built


def test_explicit_deepgram_model_selection_is_honoured(tmp_path):
    # A user who picks Deepgram + a non-default model in the Models tab must get
    # THAT model on the actual client (not the environment default).
    deepgram = FakeDeepgramClient()
    container = make_worker_container(tmp_path, deepgram=deepgram)
    ms = normalize_model_settings(primary_provider="deepgram", primary_model="nova-2")
    _seed_models(container.repositories, ms=ms, credentials={}, deepgram_key="dg-key")
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert deepgram.model == "nova-2"  # client built with the user's model
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert transcript.json_payload["model"] == "nova-2"


def test_cloud_provider_falls_back_to_deepgram_when_primary_key_missing(tmp_path):
    deepgram = FakeDeepgramClient()
    container = make_worker_container(
        tmp_path, deepgram=deepgram, build_cloud_provider=FakeCloudProvider().builder
    )
    ms = normalize_model_settings(
        primary_provider="openrouter", primary_model="openai/whisper-large-v3",
        fallback_enabled=True, fallback_provider="deepgram", fallback_model="nova-3",
    )
    # No openrouter key, but a deepgram key is available for the fallback.
    _seed_models(container.repositories, ms=ms, credentials={"deepgram": "dg-key"})
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert transcript.json_payload["provider"] == "deepgram"
    assert deepgram.api_key == "dg-key"


def test_cloud_provider_fails_friendly_without_key_or_fallback(tmp_path):
    container = make_worker_container(
        tmp_path, build_cloud_provider=FakeCloudProvider().builder
    )
    ms = normalize_model_settings(
        primary_provider="gemini", primary_model="gemini-2.5-flash"
    )
    _seed_models(container.repositories, ms=ms, credentials={})
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "Gemini" in done.error_message  # friendly, names the provider
    assert container.repositories.transcripts.get_by_job(job.id) is None


def test_rate_limit_schedules_retry_with_backoff(tmp_path):
    container = make_worker_container(
        tmp_path, deepgram=RaisingDeepgram(DeepgramRateLimitError(retry_after_seconds=90)),
        queue=InMemoryTranscriptionQueue(),
    )
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.PENDING.value
    assert done.last_error_code == "RATE_LIMIT"
    assert done.next_retry_at is not None
    assert container.queue.dead_job_ids() == set()  # retry, not dead-lettered


def test_terminal_key_invalid_dead_letters_without_retry(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(
        tmp_path, deepgram=RaisingDeepgram(ProviderKeyInvalidError()), queue=queue,
    )
    _seed(container.repositories)
    job = _claim_one(container.repositories)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert done.last_error_code == "KEY_INVALID"
    assert queue.dead_job_ids() == {job.id}  # routed to the dead-letter set


def test_retryable_error_dead_letters_once_attempts_are_exhausted(tmp_path):
    queue = InMemoryTranscriptionQueue()
    container = make_worker_container(
        tmp_path,
        deepgram=RaisingDeepgram(DeepgramRateLimitError(retry_after_seconds=1)),
        queue=queue,
    )
    container = _with_max_attempts(container, 3)
    _seed(container.repositories)
    job_row = container.repositories.jobs.create_job(7, "src-1", "a.mp4", _now())
    # Simulate having already used the attempt budget: attempts==3 at claim time.
    container.repositories.jobs._jobs[job_row.id].attempts = 2
    job = container.repositories.jobs.claim_job(job_row.id, "w", _now())  # attempts -> 3

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value          # cap reached -> terminal
    assert queue.dead_job_ids() == {job.id}


def test_backoff_grows_and_is_floored_by_retry_after():
    assert _backoff(1, 60, 3600, None) == 60
    assert _backoff(2, 60, 3600, None) == 120
    assert _backoff(3, 60, 3600, None) == 240
    assert _backoff(10, 60, 3600, None) == 3600           # capped at max
    assert _backoff(1, 60, 3600, 200) == 200              # floored by Retry-After
    # A huge Retry-After must still respect the maximum cap (no multi-day parking).
    assert _backoff(1, 60, 3600, 100000) == 3600


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
