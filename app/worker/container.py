from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.ports import Repositories
from app.deepgram_client import DeepgramClient
from app.errors import ProviderUnavailableError
from app.drive_client import DriveClient
from app.google_auth import credentials_from_token
from app.queue import QueueSettings, build_queue
from app.repositories import build_repositories
from app.transcription.config import TranscriptionConfig
from app.transcription.factory import build_local_provider as _build_local_provider
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
    # build_cloud_provider(provider_id, *, api_key, model) -> TranscriptionProvider
    # for OpenRouter/Gemini. None falls back to the legacy Deepgram/local path.
    build_cloud_provider: Callable | None = None
    queue: object | None = None
    queue_lock_ttl: int = 14400


def build_container(settings: WorkerSettings | None = None) -> WorkerContainer:
    worker_settings = settings or WorkerSettings.from_env()
    repositories = build_repositories(worker_settings.repository_backend)
    transcription_config = TranscriptionConfig.from_env()
    queue_settings = QueueSettings.from_env()
    queue = build_queue(queue_settings)

    def build_drive_client(credentials, source_folder_id, destination_folder_id):
        return DriveClient.from_credentials(
            credentials, source_folder_id, destination_folder_id
        )

    def build_deepgram_client(api_key: str, model: str | None = None):
        # model override honours a per-user Deepgram model from the Models tab;
        # None keeps the environment default (legacy behaviour).
        return DeepgramClient.from_api_key(
            api_key,
            model=model or worker_settings.deepgram_model,
            language=worker_settings.deepgram_language,
            smart_format=worker_settings.deepgram_smart_format,
            punctuate=worker_settings.deepgram_punctuate,
            diarize=worker_settings.deepgram_diarize,
            utterances=worker_settings.deepgram_utterances,
        )

    def build_cloud_provider(provider_id: str, *, api_key: str, model: str):
        # Lazy imports: keep the cloud SDK surface out of unrelated worker paths.
        # language=None lets each provider auto-detect (the worker's deepgram_language
        # like "pt-BR" is not a valid code for whisper-style cloud models).
        if provider_id == "openrouter":
            from app.transcription.openrouter_provider import OpenRouterProvider

            return OpenRouterProvider(api_key=api_key, model=model, language=None)
        if provider_id == "gemini":
            from app.transcription.gemini_provider import GeminiProvider

            return GeminiProvider(api_key=api_key, model=model, language=None)
        raise ProviderUnavailableError(
            f"Unsupported cloud provider {provider_id!r}", provider=provider_id
        )

    return WorkerContainer(
        settings=worker_settings,
        repositories=repositories,
        build_drive_client=build_drive_client,
        build_deepgram_client=build_deepgram_client,
        credentials_from_token=credentials_from_token,
        transcription_config=transcription_config,
        build_local_provider=_build_local_provider,
        build_cloud_provider=build_cloud_provider,
        queue=queue,
        queue_lock_ttl=queue_settings.global_lock_ttl_seconds,
    )
