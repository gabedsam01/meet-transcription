from pathlib import Path

from app.worker.config import WorkerSettings


def test_defaults_when_env_is_empty():
    settings = WorkerSettings.from_env({})
    assert settings.repository_backend == "postgres"
    assert settings.poll_interval_seconds == 10
    assert settings.concurrency == 1
    assert settings.stale_job_timeout_minutes == 60
    assert settings.tmp_dir == Path("/app/tmp")


def test_reads_overrides_and_lowercases_backend():
    settings = WorkerSettings.from_env(
        {
            "WORKER_REPOSITORY_BACKEND": "Memory",
            "WORKER_POLL_INTERVAL_SECONDS": "5",
            "WORKER_CONCURRENCY": "3",
            "STALE_JOB_TIMEOUT_MINUTES": "15",
            "TMP_DIR": "/tmp/worker",
            "DEEPGRAM_MODEL": "nova-2",
        }
    )
    assert settings.repository_backend == "memory"
    assert settings.poll_interval_seconds == 5
    assert settings.concurrency == 3
    assert settings.stale_job_timeout_minutes == 15
    assert settings.tmp_dir == Path("/tmp/worker")
    assert settings.deepgram_model == "nova-2"
