import pytest
import os
import json
from pathlib import Path
from app.errors import (
    ProviderCredentialInvalidError,
    ProviderCredentialMissingError,
    ProviderFileTooLargeError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.transcription.assemblyai_provider import AssemblyAIProvider
from app.transcription.provider_config import normalize_model_settings
from app.transcription.registry import resolve_cloud_provider


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
    def __init__(self, responses=None, raises=None):
        self.responses = responses or []
        self.raises = raises
        self.calls = []
        self.post_count = 0
        self.get_count = 0

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self.raises is not None:
            raise self.raises
        # Select response
        if self.post_count < len(self.responses):
            res = self.responses[self.post_count]
            self.post_count += 1
            return res
        return FakeResponse(200)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if self.raises is not None:
            raise self.raises
        # Select response from the remaining ones or return last
        idx = self.post_count + self.get_count
        if idx < len(self.responses):
            res = self.responses[idx]
            self.get_count += 1
            return res
        return FakeResponse(200)


def _media(tmp_path, content=b"mp4 bytes"):
    path = tmp_path / "meet.mp4"
    path.write_bytes(content)
    return path


def _provider(session, **over):
    kwargs = dict(
        api_key="aai-key",
        model="universal-3-pro",
        session=session,
        speaker_labels=True,
    )
    kwargs.update(over)
    return AssemblyAIProvider(**kwargs)


def test_registry_resolves_assemblyai():
    def stub_build(provider_id, model, api_key):
        return AssemblyAIProvider(api_key=api_key, model=model)

    ms = normalize_model_settings(
        primary_provider="assemblyai", primary_model="universal-3-pro"
    )
    resolved = resolve_cloud_provider(ms, {"assemblyai": "aai-key"}, build=stub_build)
    assert resolved.provider_id == "assemblyai"
    assert resolved.model == "universal-3-pro"
    assert resolved.provider._api_key == "aai-key"


def test_missing_key_raises_credential_missing():
    if "ASSEMBLYAI_API_KEY" in os.environ:
        del os.environ["ASSEMBLYAI_API_KEY"]
    with pytest.raises(ProviderCredentialMissingError):
        AssemblyAIProvider(api_key="", model="universal-3-pro", session=FakeSession())


def test_env_fallback_key():
    os.environ["ASSEMBLYAI_API_KEY"] = "env-aai-key"
    try:
        provider = AssemblyAIProvider(api_key="", model="universal-3-pro", session=FakeSession())
        assert provider._api_key == "env-aai-key"
    finally:
        del os.environ["ASSEMBLYAI_API_KEY"]


def test_payload_sends_speaker_labels_and_speakers_expected(tmp_path):
    # Setup responses for upload, submit, get (polling completed)
    responses = [
        FakeResponse(200, {"upload_url": "https://aai/uploaded"}),
        FakeResponse(200, {"id": "tx-123"}),
        FakeResponse(200, {"status": "completed", "text": "Hello", "utterances": []})
    ]
    session = FakeSession(responses)
    provider = _provider(session, speaker_labels=True, speakers_expected=3)
    
    os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"] = "0"
    try:
        provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    finally:
        del os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"]

    # Verify calls
    assert len(session.calls) == 3
    # 1. upload post
    assert session.calls[0][0] == "POST"
    assert session.calls[0][1] == "https://api.assemblyai.com/v2/upload"
    # 2. submit post
    assert session.calls[1][0] == "POST"
    assert session.calls[1][1] == "https://api.assemblyai.com/v2/transcript"
    post_data = session.calls[1][2]["json"]
    assert post_data["audio_url"] == "https://aai/uploaded"
    assert post_data["speaker_labels"] is True
    assert post_data["speakers_expected"] == 3
    assert post_data["speech_model"] == "universal-3-pro"


def test_transcribe_with_utterances_normalizes_speakers(tmp_path):
    raw_response = {
        "status": "completed",
        "text": "Hello world. How are you?",
        "language": "en",
        "utterances": [
            {"start": 1000, "end": 3000, "speaker": "A", "text": "Hello world."},
            {"start": 3500, "end": 5000, "speaker": "B", "text": "How are you?"},
        ],
        "words": [
            {"word": "Hello", "start": 1000, "end": 1500, "speaker": "A"},
            {"word": "world", "start": 1600, "end": 2000, "speaker": "A"},
        ]
    }
    responses = [
        FakeResponse(200, {"upload_url": "https://aai/uploaded"}),
        FakeResponse(200, {"id": "tx-123"}),
        FakeResponse(200, raw_response)
    ]
    session = FakeSession(responses)
    provider = _provider(session)

    os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"] = "0"
    try:
        result = provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    finally:
        del os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"]

    assert result.payload["provider"] == "assemblyai"
    assert result.payload["text"] == "Hello world. How are you?"
    assert len(result.payload["segments"]) == 2
    assert result.payload["segments"][0]["speaker"] == "Speaker A"
    assert result.payload["segments"][0]["raw_speaker"] == "A"
    assert result.payload["segments"][0]["start"] == 1.0
    assert result.payload["segments"][0]["end"] == 3.0
    
    assert len(result.payload["words"]) == 2
    assert result.payload["words"][0]["speaker"] == "Speaker A"
    assert result.payload["words"][0]["start"] == 1.0


def test_transcribe_without_utterances_text_only(tmp_path):
    raw_response = {
        "status": "completed",
        "text": "Hello world without speakers.",
        "language": "en",
        "utterances": None,
        "words": []
    }
    responses = [
        FakeResponse(200, {"upload_url": "https://aai/uploaded"}),
        FakeResponse(200, {"id": "tx-123"}),
        FakeResponse(200, raw_response)
    ]
    session = FakeSession(responses)
    provider = _provider(session)

    os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"] = "0"
    try:
        result = provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    finally:
        del os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"]

    assert len(result.payload["segments"]) == 1
    assert result.payload["segments"][0]["speaker"] is None
    assert result.payload["segments"][0]["text"] == "Hello world without speakers."


def test_polling_completed_after_pending_ticks(tmp_path):
    responses = [
        FakeResponse(200, {"upload_url": "https://aai/uploaded"}),
        FakeResponse(200, {"id": "tx-123"}),
        # Polling states
        FakeResponse(200, {"status": "queued"}),
        FakeResponse(200, {"status": "processing"}),
        FakeResponse(200, {"status": "completed", "text": "Success!"}),
    ]
    session = FakeSession(responses)
    provider = _provider(session)

    os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"] = "0"
    try:
        result = provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    finally:
        del os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"]

    assert result.payload["text"] == "Success!"
    assert session.get_count == 3


def test_polling_error_raises_response_error(tmp_path):
    responses = [
        FakeResponse(200, {"upload_url": "https://aai/uploaded"}),
        FakeResponse(200, {"id": "tx-123"}),
        FakeResponse(200, {"status": "error", "error": "Internal server crash during transcription"}),
    ]
    session = FakeSession(responses)
    provider = _provider(session)

    os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"] = "0"
    try:
        with pytest.raises(ProviderResponseError) as exc:
            provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
        assert "Internal server crash" in str(exc.value)
    finally:
        del os.environ["ASSEMBLYAI_POLL_INTERVAL_SECONDS"]


def test_429_is_retryable_rate_limit(tmp_path):
    responses = [
        FakeResponse(429, headers={"retry-after": "120"}),
    ]
    session = FakeSession(responses)
    provider = _provider(session)

    with pytest.raises(ProviderRateLimitedError) as exc:
        provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    assert exc.value.retryable is True
    assert exc.value.retry_after_seconds == 120


def test_api_key_not_logged_in_technical_message(tmp_path):
    session = FakeSession(raises=ConnectionError("assemblyai api down"))
    provider = _provider(session, api_key="super-secret-assemblyai-key-12345")
    with pytest.raises(ProviderUnavailableError) as exc:
        provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    assert "super-secret-assemblyai-key-12345" not in exc.value.technical_message
