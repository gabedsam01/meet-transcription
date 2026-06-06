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
                 fail_download=False, fail_upload=False, fail_list=False):
        self.files = list(files or [])
        self.upload_result = upload_result
        self.fail_download = fail_download
        self.fail_upload = fail_upload
        self.fail_list = fail_list
        self.downloaded: list[str] = []
        self.uploaded: list[str] = []

    def list_video_files(self):
        if self.fail_list:
            raise RuntimeError("drive list failed")
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

    def transcribe(self, video_path, api_key=None):
        if self.fail:
            raise RuntimeError("deepgram failed")
        return self.response


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
    queue=None,
):
    repositories = repositories if repositories is not None else build_memory_repositories()
    drive = drive if drive is not None else FakeDriveClient()
    deepgram = deepgram if deepgram is not None else FakeDeepgramClient()

    def build_deepgram(api_key):
        deepgram.api_key = api_key
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
        queue=queue,
    )
