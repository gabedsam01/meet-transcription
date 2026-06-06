from types import SimpleNamespace

import pytest

from app.deepgram_client import DeepgramClient, DeepgramError
from app.errors import (
    DeepgramRateLimitError,
    FileTooLargeError,
    ProviderKeyInvalidError,
)


def test_deepgram_client_posts_video_with_expected_params(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(200, {"results": {}}))
    client = DeepgramClient(
        api_key="dg-key",
        model="nova-3",
        language="pt-BR",
        smart_format=True,
        punctuate=True,
        diarize=True,
        utterances=True,
        session=session,
    )

    assert client.transcribe(video) == {"results": {}}

    request = session.requests[0]
    assert request["url"] == "https://api.deepgram.com/v1/listen"
    assert request["headers"]["Authorization"] == "Token dg-key"
    assert request["headers"]["Content-Type"] == "video/mp4"
    assert request["params"] == {
        "model": "nova-3",
        "language": "pt-BR",
        "smart_format": "true",
        "punctuate": "true",
        "diarize": "true",
        "utterances": "true",
    }
    assert request["data"] == b"mp4 bytes"


def test_deepgram_client_raises_clear_error_on_non_2xx(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(500, {"error": "server"}, text="server error"))
    client = DeepgramClient.from_settings(_settings(session))

    with pytest.raises(DeepgramError, match="Deepgram request failed with status 500"):
        client.transcribe(video)


def _settings(session):
    return SimpleNamespace(
        deepgram_api_key="dg-key",
        deepgram_model="nova-3",
        deepgram_language="pt-BR",
        deepgram_smart_format=True,
        deepgram_punctuate=True,
        deepgram_diarize=True,
        deepgram_utterances=True,
        session=session,
    )


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def post(self, url, headers, params, data, timeout):
        self.requests.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "data": data.read(),
                "timeout": timeout,
            }
        )
        return self.response


class FakeResponse:
    def __init__(self, status_code, payload, text="", headers=None):
        self.status_code = status_code
        self.payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self.payload


def _client_with(session):
    return DeepgramClient.from_api_key("dg-key", session=session)


def test_429_maps_to_retryable_rate_limit_error_with_retry_after(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(429, {}, text="slow down", headers={"Retry-After": "12"}))
    with pytest.raises(DeepgramRateLimitError) as exc:
        _client_with(session).transcribe(video)
    assert exc.value.retry_after_seconds == 12
    assert exc.value.retryable is True


def test_401_and_403_map_to_terminal_key_invalid(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    for status in (401, 403):
        session = FakeSession(FakeResponse(status, {}, text="nope"))
        with pytest.raises(ProviderKeyInvalidError):
            _client_with(session).transcribe(video)


def test_413_maps_to_terminal_file_too_large(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(413, {}, text="too big"))
    with pytest.raises(FileTooLargeError):
        _client_with(session).transcribe(video)


def test_other_5xx_still_raises_plain_deepgram_error(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(500, {}, text="server error"))
    with pytest.raises(DeepgramError, match="status 500"):
        _client_with(session).transcribe(video)


def test_missing_retry_after_header_yields_none(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(429, {}, text="slow down"))
    with pytest.raises(DeepgramRateLimitError) as exc:
        _client_with(session).transcribe(video)
    assert exc.value.retry_after_seconds is None


def test_from_api_key_builds_client_with_options():
    client = DeepgramClient.from_api_key("user-key", model="nova-3", language="pt-BR")
    assert client.api_key == "user-key"
    assert client.model == "nova-3"


def test_transcribe_per_call_api_key_overrides_instance_key(tmp_path):
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"mp4 bytes")
    session = FakeSession(FakeResponse(200, {"results": {}}))
    client = DeepgramClient.from_api_key("instance-key", session=session)

    client.transcribe(video, api_key="per-call-key")

    assert session.requests[0]["headers"]["Authorization"] == "Token per-call-key"
