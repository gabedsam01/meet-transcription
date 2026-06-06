import pytest

from app.errors import (
    AppError,
    DeepgramKeyRequiredError,
    DeepgramRateLimitError,
    DriveFolderMissingError,
    FileTooLargeError,
    GoogleTokenMissingError,
    JobAlreadyProcessingError,
    JobAlreadyQueuedError,
    LocalTranscriptionConfigError,
    LocalTranscriptionUnavailableError,
    ModelNotFoundError,
    ProviderKeyInvalidError,
    QueueLockError,
    QueueUnavailableError,
    TranscriptionProviderError,
    WhisperCppBinaryNotFoundError,
    classify_error,
)


def test_app_error_carries_user_message_and_is_runtime_error():
    err = AppError("technical detail for logs", user_message="Algo deu errado.")
    assert isinstance(err, RuntimeError)
    assert err.user_message == "Algo deu errado."
    assert "technical detail for logs" in str(err)


def test_default_user_message_is_friendly_and_non_empty():
    for cls in (
        LocalTranscriptionUnavailableError,
        DeepgramKeyRequiredError,
        QueueUnavailableError,
        ModelNotFoundError,
        GoogleTokenMissingError,
    ):
        err = cls()
        assert isinstance(err, AppError)
        assert err.user_message and isinstance(err.user_message, str)


def test_hierarchy_groups_provider_and_config_errors():
    assert issubclass(LocalTranscriptionUnavailableError, TranscriptionProviderError)
    assert issubclass(DeepgramKeyRequiredError, TranscriptionProviderError)
    assert issubclass(LocalTranscriptionConfigError, TranscriptionProviderError)
    assert issubclass(ModelNotFoundError, LocalTranscriptionConfigError)
    assert issubclass(WhisperCppBinaryNotFoundError, LocalTranscriptionConfigError)
    for cls in (
        QueueUnavailableError,
        QueueLockError,
        GoogleTokenMissingError,
        DriveFolderMissingError,
        JobAlreadyQueuedError,
        JobAlreadyProcessingError,
    ):
        assert issubclass(cls, AppError)


def test_factory_alias_points_at_the_canonical_error():
    from app.transcription.factory import LocalTranscriptionUnavailable

    assert LocalTranscriptionUnavailable is LocalTranscriptionUnavailableError


def test_pytest_raises_matches_on_message():
    with pytest.raises(ModelNotFoundError, match="MODEL_PATH"):
        raise ModelNotFoundError("missing LOCAL_TRANSCRIPTION_MODEL_PATH")


# --- retry classification ---------------------------------------------------


def test_app_error_is_terminal_by_default():
    err = AppError("x")
    assert err.error_code == "UNEXPECTED"
    assert err.retryable is False


def test_rate_limit_error_is_retryable_and_carries_retry_after():
    err = DeepgramRateLimitError(retry_after_seconds=30)
    assert err.error_code == "RATE_LIMIT"
    assert err.retryable is True
    assert err.retry_after_seconds == 30
    assert issubclass(DeepgramRateLimitError, TranscriptionProviderError)


def test_key_invalid_and_file_too_large_are_terminal():
    assert ProviderKeyInvalidError().error_code == "KEY_INVALID"
    assert ProviderKeyInvalidError().retryable is False
    assert FileTooLargeError().error_code == "FILE_TOO_LARGE"
    assert FileTooLargeError().retryable is False


def test_config_errors_are_terminal():
    for cls in (
        LocalTranscriptionUnavailableError,
        DeepgramKeyRequiredError,
        LocalTranscriptionConfigError,
        ModelNotFoundError,
    ):
        assert cls().retryable is False
        assert cls().error_code == "CONFIG"


def test_classify_error_reads_apperror_fields():
    code, retryable, after = classify_error(DeepgramRateLimitError(retry_after_seconds=12))
    assert (code, retryable, after) == ("RATE_LIMIT", True, 12)
    code, retryable, after = classify_error(ProviderKeyInvalidError())
    assert (code, retryable, after) == ("KEY_INVALID", False, None)


def test_classify_unknown_exception_is_retryable():
    code, retryable, after = classify_error(ValueError("boom"))
    assert code == "UNEXPECTED"
    assert retryable is True
    assert after is None
