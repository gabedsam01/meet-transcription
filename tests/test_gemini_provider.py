import pytest

from app.errors import (
    ProviderCredentialInvalidError,
    ProviderCredentialMissingError,
    ProviderFileTooLargeError,
    ProviderRateLimitedError,
)
from app.transcription.gemini_provider import (
    GeminiProvider,
    select_gemini_upload_mode,
)
from app.transcription.provider_models import (
    GEMINI_FILES_MAX_BYTES,
    GEMINI_INLINE_MAX_BYTES,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json


class FakeSession:
    """Returns queued responses in order (supports the 2-step Files API flow)."""

    def __init__(self, *responses, raises=None):
        self._responses = list(responses)
        self.raises = raises
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.raises is not None:
            raise self.raises
        return self._responses.pop(0)


def _gemini_text_response(text):
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def _media(tmp_path, content=b"abc"):
    path = tmp_path / "meet.mp4"
    path.write_bytes(content)
    return path


# --- size limit (the spec's 70 MB inline / 99 MB Files boundaries) ----------


def test_upload_mode_uses_real_constants():
    assert GEMINI_INLINE_MAX_BYTES == 70 * 1024 * 1024
    assert GEMINI_FILES_MAX_BYTES == 99 * 1024 * 1024
    assert select_gemini_upload_mode(GEMINI_INLINE_MAX_BYTES) == "inline"
    assert select_gemini_upload_mode(GEMINI_INLINE_MAX_BYTES + 1) == "files"
    assert select_gemini_upload_mode(GEMINI_FILES_MAX_BYTES) == "files"
    assert select_gemini_upload_mode(GEMINI_FILES_MAX_BYTES + 1) == "too_large"


def test_inline_limit_70mb_switches_to_files_api(tmp_path):
    # A file just over the inline budget must NOT go inline.
    session = FakeSession(
        FakeResponse(200, {"file": {"uri": "files/abc"}}),
        _gemini_text_response("transcrição"),
    )
    provider = GeminiProvider(
        api_key="g", model="gemini-2.5-flash", session=session,
        inline_max_bytes=4, files_max_bytes=100,
    )
    result = provider.transcribe(
        _media(tmp_path, b"0123456789"), original_name="m.mp4", file_id="x"
    )
    assert result.payload["provider"] == "gemini"
    assert result.payload["text"] == "transcrição"
    # Two HTTP calls: upload + generateContent (the Files API path).
    assert len(session.calls) == 2
    assert "upload" in session.calls[0][0]


def test_files_limit_99mb_rejects_oversize(tmp_path):
    provider = GeminiProvider(
        api_key="g", model="gemini-2.5-flash", session=FakeSession(),
        inline_max_bytes=4, files_max_bytes=8,
    )
    with pytest.raises(ProviderFileTooLargeError):
        provider.transcribe(
            _media(tmp_path, b"0123456789"), original_name="m.mp4", file_id="x"
        )


# --- inline happy path + error mapping --------------------------------------


def test_inline_transcription_normalizes(tmp_path):
    session = FakeSession(_gemini_text_response("Locutor 1: olá"))
    provider = GeminiProvider(api_key="g", model="gemini-2.5-flash", session=session)
    result = provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
    assert result.payload["text"] == "Locutor 1: olá"
    assert result.payload["segments"][0]["speaker"] is None  # never real diarization
    assert "TRANSCRIÇÃO DA REUNIÃO" in result.text
    assert len(session.calls) == 1  # inline = single call


def test_missing_key_raises_credential_missing():
    with pytest.raises(ProviderCredentialMissingError):
        GeminiProvider(api_key=" ", model="gemini-2.5-flash", session=FakeSession())


def test_401_is_invalid_credential(tmp_path):
    provider = GeminiProvider(
        api_key="g", model="gemini-2.5-flash", session=FakeSession(FakeResponse(401))
    )
    with pytest.raises(ProviderCredentialInvalidError):
        provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")


def test_429_is_rate_limited(tmp_path):
    provider = GeminiProvider(
        api_key="g", model="gemini-2.5-flash", session=FakeSession(FakeResponse(429))
    )
    with pytest.raises(ProviderRateLimitedError):
        provider.transcribe(_media(tmp_path), original_name="m.mp4", file_id="x")
