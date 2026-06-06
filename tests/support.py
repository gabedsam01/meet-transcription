from __future__ import annotations

from pathlib import Path

from app.processor import DriveFile
from app.repositories.memory import build_memory_repositories
from app.worker.config import WorkerSettings
from app.worker.container import WorkerContainer


def drive_file(file_id: str, name: str = "meeting.mp4") -> DriveFile:
    return DriveFile(
        id=file_id, name=name, mime_type="video/mp4", size=10,
        created_time="2026-06-03T10:00:00Z", modified_time="2026-06-03T10:00:00Z",
    )


class FakeDriveClient:
    def __init__(self, files=None, upload_result="drive-txt-1",
                 fail_download=False, fail_upload=False):
        self.files = list(files or [])
        self.upload_result = upload_result
        self.fail_download = fail_download
        self.fail_upload = fail_upload
        self.downloaded: list[str] = []
        self.uploaded: list[str] = []

    def list_video_files(self):
        return list(self.files)

    def download_by_id(self, file_id, destination):
        if self.fail_download:
            raise RuntimeError("download failed")
        self.downloaded.append(file_id)
        Path(destination).write_bytes(b"mp4 bytes")

    def upload_text_file(self, source_path, filename):
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.uploaded.append(filename)
        return self.upload_result


class FakeDeepgramClient:
    def __init__(self, response=None, fail=False):
        self.response = response or {
            "results": {"utterances": [
                {"start": 1.0, "speaker": 0, "transcript": "Ola mundo."}
            ]}
        }
        self.fail = fail
        self.api_key = None
        self.model = None  # last model the client was built with

    def transcribe(self, video_path, api_key=None):
        if self.fail:
            raise RuntimeError("deepgram failed")
        return self.response


class FakeCloudProvider:
    """Stand-in for OpenRouter/Gemini in worker tests; records how it was built."""

    def __init__(self, provider_id="openrouter"):
        self.provider_id = provider_id
        self.built = []  # (provider_id, api_key, model)
        self.calls = []

    def builder(self, provider_id, *, api_key, model):
        self.built.append((provider_id, api_key, model))
        return self

    def transcribe(self, source_path, *, original_name, file_id):
        from app.transcription.provider import TranscriptionResult

        self.calls.append((str(source_path), original_name, file_id))
        return TranscriptionResult(
            text="CLOUD TXT Olá",
            payload={
                "provider": self.provider_id, "engine": self.provider_id,
                "model": "m", "language": "pt", "text": "Olá",
                "segments": [], "words": [], "utterances": [], "raw": {},
            },
        )


def make_worker_settings(tmp_dir, **overrides) -> WorkerSettings:
    base = dict(
        repository_backend="memory", poll_interval_seconds=1, concurrency=1,
        stale_job_timeout_minutes=60, tmp_dir=Path(tmp_dir),
        deepgram_model="nova-3", deepgram_language="pt-BR",
        deepgram_smart_format=True, deepgram_punctuate=True,
        deepgram_diarize=True, deepgram_utterances=True,
    )
    base.update(overrides)
    return WorkerSettings(**base)


def make_worker_container(
    tmp_dir,
    repositories=None,
    drive=None,
    deepgram=None,
    transcription_config=None,
    transcription_probes=None,
    build_local_provider=None,
    build_cloud_provider=None,
    queue=None,
    audio_config=None,
    audio_runner=None,
    diarization_config=None,
    diarization_probes=None,
    build_diarization_provider=None,
    recordings_dir=None,
):
    repositories = repositories if repositories is not None else build_memory_repositories()
    drive = drive if drive is not None else FakeDriveClient()
    deepgram = deepgram if deepgram is not None else FakeDeepgramClient()

    def build_deepgram(api_key, model=None):
        deepgram.api_key = api_key
        deepgram.model = model
        return deepgram

    return WorkerContainer(
        settings=make_worker_settings(tmp_dir),
        repositories=repositories,
        build_drive_client=lambda credentials, src, dst: drive,
        build_deepgram_client=build_deepgram,
        credentials_from_token=lambda token: object(),
        transcription_config=transcription_config,
        transcription_probes=transcription_probes,
        build_local_provider=build_local_provider,
        build_cloud_provider=build_cloud_provider,
        queue=queue,
        audio_config=audio_config,
        audio_runner=audio_runner,
        diarization_config=diarization_config,
        diarization_probes=diarization_probes,
        build_diarization_provider=build_diarization_provider,
        recordings_dir=recordings_dir,
    )
