from types import SimpleNamespace

import pytest

from app.deepgram_client import DeepgramClient, DeepgramError


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
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        return self.payload
