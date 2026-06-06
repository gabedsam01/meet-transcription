from app.errors import (
    AppError,
    DeepgramKeyRequiredError,
    LocalTranscriptionConfigError,
    QueueUnavailableError,
    error_code,
    is_retryable,
)


def test_base_error_has_safe_defaults():
    assert AppError.code == "app_error"
    assert AppError().retryable is False
    assert AppError().doc_url is None


def test_subclasses_expose_stable_codes_and_flags():
    assert DeepgramKeyRequiredError().code == "deepgram_key_required"
    assert DeepgramKeyRequiredError().retryable is False
    assert DeepgramKeyRequiredError().doc_url.endswith("05-deepgram.md")
    assert LocalTranscriptionConfigError().code == "local_transcription_invalid"
    assert QueueUnavailableError().retryable is True


def test_error_code_helper_handles_non_apperrors():
    assert error_code(DeepgramKeyRequiredError()) == "deepgram_key_required"
    assert error_code(ValueError("boom")) == "internal_error"


def test_is_retryable_helper():
    assert is_retryable(QueueUnavailableError()) is True
    assert is_retryable(DeepgramKeyRequiredError()) is False
    assert is_retryable(ValueError("boom")) is False


def test_user_message_present_and_secret_free():
    err = DeepgramKeyRequiredError()
    assert "Deepgram" in err.user_message
    # user_message must never contain a key/token-looking secret.
    assert "sk-" not in err.user_message
