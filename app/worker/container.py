from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.ports import Repositories
from app.deepgram_client import DeepgramClient
from app.drive_client import DriveClient
from app.google_auth import credentials_from_token
from app.repositories import build_repositories
from app.worker.config import WorkerSettings


@dataclass
class WorkerContainer:
    settings: WorkerSettings
    repositories: Repositories
    build_drive_client: Callable
    build_deepgram_client: Callable
    credentials_from_token: Callable


def build_container(settings: WorkerSettings | None = None) -> WorkerContainer:
    worker_settings = settings or WorkerSettings.from_env()
    repositories = build_repositories(worker_settings.repository_backend)

    def build_drive_client(credentials, source_folder_id, destination_folder_id):
        return DriveClient.from_credentials(
            credentials, source_folder_id, destination_folder_id
        )

    def build_deepgram_client(api_key: str):
        return DeepgramClient.from_api_key(
            api_key,
            model=worker_settings.deepgram_model,
            language=worker_settings.deepgram_language,
            smart_format=worker_settings.deepgram_smart_format,
            punctuate=worker_settings.deepgram_punctuate,
            diarize=worker_settings.deepgram_diarize,
            utterances=worker_settings.deepgram_utterances,
        )

    return WorkerContainer(
        settings=worker_settings,
        repositories=repositories,
        build_drive_client=build_drive_client,
        build_deepgram_client=build_deepgram_client,
        credentials_from_token=credentials_from_token,
    )
