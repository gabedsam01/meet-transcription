from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.ports import Repositories
from app.deepgram_client import DeepgramClient
from app.drive_client import DriveClient
from app.google_auth import credentials_from_token
from app.queue import QueueSettings, build_queue
from app.repositories import build_repositories
from app.transcription.config import TranscriptionConfig
from app.transcription.factory import build_local_provider as _build_local_provider
from app.webhooks import WebhookNotifier, WebhookSettings
from app.worker.config import WorkerSettings


@dataclass
class WorkerContainer:
    settings: WorkerSettings
    repositories: Repositories
    build_drive_client: Callable
    build_deepgram_client: Callable
    credentials_from_token: Callable
    # Local transcription + queue wiring. All optional with disabled/None defaults
    # so test helpers and the legacy Deepgram-only path keep working unchanged.
    transcription_config: TranscriptionConfig | None = None
    transcription_probes: object | None = None
    build_local_provider: Callable | None = None
    queue: object | None = None
    queue_lock_ttl: int = 14400
    # Optional outbound webhooks (job.completed / job.failed). None = disabled.
    webhook_notifier: object | None = None


def build_container(settings: WorkerSettings | None = None) -> WorkerContainer:
    worker_settings = settings or WorkerSettings.from_env()
    repositories = build_repositories(worker_settings.repository_backend)
    transcription_config = TranscriptionConfig.from_env()
    queue_settings = QueueSettings.from_env()
    queue = build_queue(queue_settings)
    webhook_settings = WebhookSettings.from_env()
    webhook_notifier = WebhookNotifier(webhook_settings) if webhook_settings.enabled else None

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
        transcription_config=transcription_config,
        build_local_provider=_build_local_provider,
        queue=queue,
        queue_lock_ttl=queue_settings.global_lock_ttl_seconds,
        webhook_notifier=webhook_notifier,
    )
