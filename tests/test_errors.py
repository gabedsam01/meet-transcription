import pytest

from app.errors import (
    AppError,
    DeepgramKeyRequiredError,
    DriveFolderMissingError,
    GoogleTokenMissingError,
    JobAlreadyProcessingError,
    JobAlreadyQueuedError,
    LocalTranscriptionConfigError,
    LocalTranscriptionUnavailableError,
    ModelNotFoundError,
    QueueLockError,
    QueueUnavailableError,
    TranscriptionProviderError,
    WhisperCppBinaryNotFoundError,
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
