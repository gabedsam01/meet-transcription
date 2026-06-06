import pytest
import os
from pathlib import Path
from app.errors import (
    ProviderCredentialInvalidError,
    ProviderCredentialMissingError,
    ProviderFileTooLargeError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.transcription.groq_provider import GroqProvider


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, raise_json=False, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self._raise = raise_json
        self.headers = headers or {}

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._json


class FakeSession:
    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.response


def _media(tmp_path, content=b"mp4 bytes"):
    path = tmp_path / "meet.mp4"
    path.write_bytes(content)
    return path


def _provider(session, **over):
    kwargs = dict(api_key="groq-key", model="whisper-large-v3-turbo", session=session)
    kwargs.update(over)
    return GroqProvider(**kwargs)


def test_missing_key_raises_credential_missing():
    # If no key in constructor and no key in env, should raise
    if "GROQ_API_KEY" in os.environ:
        del os.environ["GROQ_API_KEY"]
    with pytest.raises(ProviderCredentialMissingError):
        GroqProvider(api_key="", model="whisper-large-v3-turbo", session=FakeSession())


def test_env_fallback_key():
    os.environ["GROQ_API_KEY"] = "env-groq-key"
    try:
        provider = GroqProvider(api_key="", model="whisper-large-v3-turbo", session=FakeSession())
        assert provider._api_key == "env-groq-key"
    finally:
        del os.environ["GROQ_API_KEY"]


def test_verbose_json_response_normalizes_segments_and_words(tmp_path):
    raw = {
        "text": "Hello world",
        "language": "english",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "Hello"},
            {"start": 2.0, "end": 4.0, "text": "world"},
        ],
        "words": [
            {"word": "Hello", "start": 0.0, "end": 1.0},
            {"word": "world", "start": 2.0, "end": 3.0},
        ]
    }
    session = FakeSession(FakeResponse(200, raw))
    result = _provider(session).transcribe(
        _media(tmp_path), original_name="meet.mp4", file_id="id-1"
    )
    assert result.payload["provider"] == "groq"
    assert result.payload["text"] == "Hello world"
    assert len(result.payload["segments"]) == 2
    assert len(result.payload["words"]) == 2
    assert result.payload["words"][0]["word"] == "Hello"
    assert result.payload["words"][0]["start"] == 0.0
    assert "TRANSCRIÇÃO DA REUNIÃO" in result.text

    url, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer groq-key"
    
    # Check that data payload includes response_format and timestamp_granularities[]
    data_dict = dict(kwargs["data"])
    assert ("response_format", "verbose_json") in kwargs["data"]
    assert ("timestamp_granularities[]", "segment") in kwargs["data"]
    assert ("timestamp_granularities[]", "word") in kwargs["data"]


def test_429_is_retryable_rate_limit_with_header(tmp_path):
    response = FakeResponse(429, headers={"retry-after": "45"})
    with pytest.raises(ProviderRateLimitedError) as exc:
        _provider(FakeSession(response)).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )
    assert exc.value.retryable is True
    assert exc.value.retry_after_seconds == 45
    assert exc.value.error_code == "provider_rate_limited"


def test_401_is_invalid_credential(tmp_path):
    with pytest.raises(ProviderCredentialInvalidError):
        _provider(FakeSession(FakeResponse(401))).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )


def test_403_is_invalid_credential(tmp_path):
    with pytest.raises(ProviderCredentialInvalidError):
        _provider(FakeSession(FakeResponse(403))).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )


def test_413_is_file_too_large(tmp_path):
    with pytest.raises(ProviderFileTooLargeError):
        _provider(FakeSession(FakeResponse(413))).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )


def test_5xx_is_unavailable_and_retryable(tmp_path):
    with pytest.raises(ProviderUnavailableError) as exc:
        _provider(FakeSession(FakeResponse(503))).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )
    assert exc.value.retryable is True


def test_oversize_file_rejected_before_upload(tmp_path):
    session = FakeSession(FakeResponse(200, {"text": "x"}))
    with pytest.raises(ProviderFileTooLargeError):
        _provider(session, max_file_bytes=4).transcribe(
            _media(tmp_path, b"0123456789"), original_name="m.mp4", file_id="x"
        )
    assert session.calls == []  # never attempted the upload


def test_api_key_not_logged_in_technical_message(tmp_path):
    session = FakeSession(raises=ConnectionError("failed to connect to api.groq.com"))
    provider = _provider(session, api_key="secret-groq-api-key-12345")
    with pytest.raises(ProviderUnavailableError) as exc:
        provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    # API key must NOT be in the exception's technical message
    assert "secret-groq-api-key-12345" not in exc.value.technical_message
