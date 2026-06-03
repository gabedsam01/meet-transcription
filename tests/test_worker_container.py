from pathlib import Path

import pytest

from app.deepgram_client import DeepgramClient
from app.repositories import RepositoryBackendError
from app.repositories.memory import InMemoryJobRepository
from app.worker.config import WorkerSettings
from app.worker.container import build_container


def _settings(backend, tmp_path):
    return WorkerSettings(
        repository_backend=backend, poll_interval_seconds=10, concurrency=1,
        stale_job_timeout_minutes=60, tmp_dir=Path(tmp_path),
        deepgram_model="nova-3", deepgram_language="pt-BR",
        deepgram_smart_format=True, deepgram_punctuate=True,
        deepgram_diarize=True, deepgram_utterances=True,
    )


def test_build_container_with_memory_backend(tmp_path):
    container = build_container(_settings("memory", tmp_path))
    assert isinstance(container.repositories.jobs, InMemoryJobRepository)

    deepgram = container.build_deepgram_client("user-key")
    assert isinstance(deepgram, DeepgramClient)
    assert deepgram.api_key == "user-key"
    assert deepgram.model == "nova-3"


def test_build_container_with_postgres_backend_fails_clearly(tmp_path):
    with pytest.raises(RepositoryBackendError):
        build_container(_settings("postgres", tmp_path))
