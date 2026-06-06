import pytest

from app.errors import (
    ProviderCredentialInvalidError,
    ProviderCredentialMissingError,
    ProviderFileTooLargeError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.transcription.openrouter_provider import OpenRouterProvider


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, raise_json=False):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self._raise = raise_json

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
    kwargs = dict(api_key="or-key", model="openai/whisper-large-v3", session=session)
    kwargs.update(over)
    return OpenRouterProvider(**kwargs)


def test_missing_key_raises_credential_missing():
    with pytest.raises(ProviderCredentialMissingError):
        OpenRouterProvider(api_key="", model="m", session=FakeSession())


def test_text_only_response_normalizes_to_single_segment(tmp_path):
    session = FakeSession(FakeResponse(200, {"text": "olá mundo"}))
    result = _provider(session).transcribe(
        _media(tmp_path), original_name="meet.mp4", file_id="id-1"
    )
    assert result.payload["provider"] == "openrouter"
    assert result.payload["text"] == "olá mundo"
    assert len(result.payload["segments"]) == 1
    assert "TRANSCRIÇÃO DA REUNIÃO" in result.text
    # request used Bearer auth and sent the model
    url, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer or-key"
    assert kwargs["data"]["model"] == "openai/whisper-large-v3"


def test_segments_response_maps_through(tmp_path):
    raw = {"text": "a b", "segments": [
        {"start": 0, "end": 1, "text": "a"}, {"start": 1, "end": 2, "text": "b"},
    ]}
    result = _provider(FakeSession(FakeResponse(200, raw))).transcribe(
        _media(tmp_path), original_name="m.mp4", file_id="x"
    )
    assert [s["text"] for s in result.payload["segments"]] == ["a", "b"]


def test_429_is_retryable_rate_limit(tmp_path):
    with pytest.raises(ProviderRateLimitedError) as exc:
        _provider(FakeSession(FakeResponse(429))).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )
    assert exc.value.retryable is True


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


def test_network_failure_is_unavailable(tmp_path):
    session = FakeSession(raises=ConnectionError("boom"))
    with pytest.raises(ProviderUnavailableError):
        _provider(session).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )


def test_invalid_json_is_response_error(tmp_path):
    with pytest.raises(ProviderResponseError):
        _provider(FakeSession(FakeResponse(200, raise_json=True))).transcribe(
            _media(tmp_path), original_name="m.mp4", file_id="x"
        )
