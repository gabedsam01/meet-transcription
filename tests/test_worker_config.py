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


def test_automation_retry_and_guardrail_defaults():
    s = WorkerSettings.from_env({})
    assert s.queue_concurrency == 5
    assert s.job_max_attempts == 3
    assert s.job_retry_base_seconds == 60
    assert s.job_retry_max_seconds == 3600
    assert s.auto_poll_enabled is False
    assert s.auto_poll_interval_seconds == 300
    assert s.auto_poll_max_users_per_tick == 50
    assert s.auto_poll_max_files_per_user == 5
    assert s.auto_poll_lock_ttl_seconds == 240
    assert s.max_file_size_mb == 0       # 0 = unlimited
    assert s.daily_jobs_limit == 0


def test_automation_retry_and_guardrail_overrides():
    s = WorkerSettings.from_env(
        {
            "TRANSCRIPTION_QUEUE_CONCURRENCY": "8",
            "JOB_MAX_ATTEMPTS": "5",
            "JOB_RETRY_BASE_SECONDS": "30",
            "JOB_RETRY_MAX_SECONDS": "1800",
            "AUTO_POLL_ENABLED": "true",
            "AUTO_POLL_INTERVAL_SECONDS": "120",
            "AUTO_POLL_MAX_USERS_PER_TICK": "10",
            "AUTO_POLL_MAX_FILES_PER_USER": "2",
            "AUTO_POLL_LOCK_TTL_SECONDS": "90",
            "MAX_FILE_SIZE_MB": "500",
            "DAILY_JOBS_LIMIT": "20",
        }
    )
    assert s.queue_concurrency == 8
    assert s.job_max_attempts == 5
    assert s.job_retry_base_seconds == 30
    assert s.job_retry_max_seconds == 1800
    assert s.auto_poll_enabled is True
    assert s.auto_poll_interval_seconds == 120
    assert s.auto_poll_max_users_per_tick == 10
    assert s.auto_poll_max_files_per_user == 2
    assert s.auto_poll_lock_ttl_seconds == 90
    assert s.max_file_size_mb == 500
    assert s.daily_jobs_limit == 20
