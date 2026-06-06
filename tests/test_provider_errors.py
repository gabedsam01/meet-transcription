"""The cloud-provider error hierarchy carries structured, secret-free metadata."""

import pytest

from app.errors import (
    AppError,
    ProviderCredentialInvalidError,
    ProviderCredentialMissingError,
    ProviderError,
    ProviderFileTooLargeError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderUnavailableError,
    TranscriptionProviderError,
)
from app.transcription import errors as provider_errors


def test_provider_errors_are_app_errors():
    assert issubclass(ProviderError, TranscriptionProviderError)
    assert issubclass(ProviderError, AppError)


def test_each_error_exposes_code_user_message_retryable_docs():
    err = ProviderCredentialMissingError("openrouter key absent", provider="openrouter")
    assert err.code == "provider_credential_missing"
    assert err.user_message  # friendly, non-empty
    assert err.technical_message == "openrouter key absent"
    assert err.retryable is False
    assert err.docs_url
    assert err.provider == "openrouter"


def test_to_dict_is_serializable_and_secret_free():
    err = ProviderCredentialInvalidError("bad token sk-123", user_message="Chave inválida.")
    data = err.to_dict()
    assert set(data) == {
        "code", "user_message", "technical_message", "retryable", "docs_url", "provider",
    }
    # user_message never echoes the technical detail (which may carry a token).
    assert "sk-123" not in data["user_message"]


def test_rate_limit_and_unavailable_are_retryable():
    assert ProviderRateLimitedError().retryable is True
    assert ProviderUnavailableError().retryable is True
    assert ProviderFileTooLargeError().retryable is False
    assert ProviderResponseError().retryable is False


def test_overrides_take_effect():
    err = ProviderError("x", retryable=True, docs_url="https://docs/x")
    assert err.retryable is True
    assert err.docs_url == "https://docs/x"


def test_reexport_matches_central_module():
    assert provider_errors.ProviderRateLimitedError is ProviderRateLimitedError


def test_can_be_raised_and_caught_as_app_error():
    with pytest.raises(AppError):
        raise ProviderUnavailableError("gemini 503")
