from app.queue.config import QueueSettings


def test_defaults_to_none_backend_and_transcription_keys():
    s = QueueSettings.from_env({})
    assert s.backend == "none"  # default keeps the legacy poll loop / existing tests
    assert s.queue_name == "transcription"
    assert s.global_lock_ttl_seconds == 14400
    assert s.redis_url == "redis://redis:6379/0"


def test_reads_overrides_and_lowercases_backend():
    s = QueueSettings.from_env(
        {
            "QUEUE_BACKEND": "Redis",
            "REDIS_URL": "redis://localhost:6379/1",
            "QUEUE_NAME": "myq",
            "TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS": "60",
        }
    )
    assert s.backend == "redis"
    assert s.redis_url == "redis://localhost:6379/1"
    assert s.queue_name == "myq"
    assert s.global_lock_ttl_seconds == 60


def test_provider_concurrency_defaults_and_overrides():
    s = QueueSettings.from_env({})
    assert s.cloud_concurrency == 5
    assert s.local_concurrency == 1
    assert s.provider_lock_ttl_seconds == 14400

    s2 = QueueSettings.from_env(
        {
            "CLOUD_TRANSCRIPTION_CONCURRENCY": "10",
            "LOCAL_TRANSCRIPTION_CONCURRENCY": "1",
            "PROVIDER_LOCK_TTL_SECONDS": "7200",
        }
    )
    assert s2.cloud_concurrency == 10
    assert s2.local_concurrency == 1
    assert s2.provider_lock_ttl_seconds == 7200
