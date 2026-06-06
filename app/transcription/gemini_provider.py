"""Google Gemini transcription provider.

Gemini is a multimodal model: it transcribes audio and can *attempt* speaker
labels via prompt, but that is never treated as real diarization. The request
path depends on file size — small files go inline (base64), larger ones via the
Files API — and anything above the Files ceiling is refused with a friendly
error (chunking is a separate concern).

HTTP is injectable for tests; this provider runs only in the worker.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from app.errors import (
    ProviderCredentialInvalidError,
    ProviderCredentialMissingError,
    ProviderError,
    ProviderFileTooLargeError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.transcription.normalizer import normalize_gemini, render_transcript_text
from app.transcription.provider import TranscriptionResult
from app.transcription.provider_models import (
    GEMINI,
    GEMINI_FILES_MAX_BYTES,
    GEMINI_INLINE_MAX_BYTES,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "Transcreva integralmente o áudio desta reunião. Use português quando o áudio "
    "estiver em português. Quando houver múltiplas vozes, indique os locutores no "
    "texto (ex.: 'Locutor 1:'), sem inventar conteúdo."
)

MODE_INLINE = "inline"
MODE_FILES = "files"
MODE_TOO_LARGE = "too_large"


def select_gemini_upload_mode(
    size_bytes: int,
    *,
    inline_max: int = GEMINI_INLINE_MAX_BYTES,
    files_max: int = GEMINI_FILES_MAX_BYTES,
) -> str:
    """Pick the request path for a file of ``size_bytes``.

    - ``inline`` for files that fit the (base64-inflated) inline budget,
    - ``files`` for larger files within the Files API budget,
    - ``too_large`` for anything beyond the Files API budget.
    """
    if size_bytes > files_max:
        return MODE_TOO_LARGE
    if size_bytes <= inline_max:
        return MODE_INLINE
    return MODE_FILES


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".webm": "video/webm",
        ".flac": "audio/flac",
    }.get(suffix, "video/mp4")


class GeminiProvider:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        language: str | None = None,
        session: Any | None = None,
        timeout: int = 600,
        inline_max_bytes: int = GEMINI_INLINE_MAX_BYTES,
        files_max_bytes: int = GEMINI_FILES_MAX_BYTES,
        prompt: str = DEFAULT_PROMPT,
        base_url: str | None = None,
        upload_url: str | None = None,
    ) -> None:
        if not (api_key or "").strip():
            raise ProviderCredentialMissingError(
                "Gemini API key is required", provider=GEMINI
            )
        self._api_key = api_key
        self._model = model
        self._language = language
        self._timeout = timeout
        self._inline_max = inline_max_bytes
        self._files_max = files_max_bytes
        self._prompt = prompt
        self._base_url = base_url or self.BASE_URL
        self._upload_url = upload_url or self.UPLOAD_URL
        if session is None:
            import requests

            session = requests
        self._session = session

    def transcribe(
        self, source_path: str | Path, *, original_name: str, file_id: str
    ) -> TranscriptionResult:
        path = Path(source_path)
        size = path.stat().st_size if path.exists() else 0
        mode = select_gemini_upload_mode(
            size, inline_max=self._inline_max, files_max=self._files_max
        )
        if mode == MODE_TOO_LARGE:
            raise ProviderFileTooLargeError(
                f"Gemini file is {size} bytes, exceeds Files API limit {self._files_max}",
                provider=GEMINI,
            )
        raw = self._generate_inline(path) if mode == MODE_INLINE else self._generate_via_files(path)
        text = _extract_text(raw)
        payload = normalize_gemini(
            text, model=self._model, language=self._language, raw=raw
        )
        rendered = render_transcript_text(payload, original_name or "", file_id or "")
        return TranscriptionResult(text=rendered, payload=payload)

    # -- internals -----------------------------------------------------------

    def _generate_inline(self, path: Path) -> dict[str, Any]:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": self._prompt},
                        {"inline_data": {"mime_type": _guess_mime(path), "data": encoded}},
                    ]
                }
            ]
        }
        response = self._request(
            "post",
            f"{self._base_url}/models/{self._model}:generateContent",
            params={"key": self._api_key},
            json=body,
        )
        self._raise_for_status(response)
        return self._parse(response)

    def _generate_via_files(self, path: Path) -> dict[str, Any]:
        # Two-step Files API flow: upload, then reference the returned file URI.
        with path.open("rb") as handle:
            upload = self._request(
                "post",
                self._upload_url,
                params={"key": self._api_key},
                headers={"X-Goog-Upload-Protocol": "raw", "Content-Type": _guess_mime(path)},
                data=handle,
            )
        self._raise_for_status(upload)
        file_uri = _extract_file_uri(self._parse(upload))
        if not file_uri:
            raise ProviderResponseError(
                "Gemini Files API did not return a file URI", provider=GEMINI
            )
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": self._prompt},
                        {"file_data": {"mime_type": _guess_mime(path), "file_uri": file_uri}},
                    ]
                }
            ]
        }
        response = self._request(
            "post",
            f"{self._base_url}/models/{self._model}:generateContent",
            params={"key": self._api_key},
            json=body,
        )
        self._raise_for_status(response)
        return self._parse(response)

    def _request(self, method: str, url: str, **kwargs):
        try:
            return getattr(self._session, method)(url, timeout=self._timeout, **kwargs)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - network failures degrade gracefully
            raise ProviderUnavailableError(
                f"Gemini request failed: {exc}", provider=GEMINI
            ) from exc

    def _raise_for_status(self, response) -> None:
        code = getattr(response, "status_code", 0)
        if 200 <= code < 300:
            return
        if code in (401, 403):
            raise ProviderCredentialInvalidError(
                f"Gemini auth failed (HTTP {code})", provider=GEMINI
            )
        if code == 429:
            raise ProviderRateLimitedError(
                "Gemini rate limited (HTTP 429)", provider=GEMINI
            )
        if code == 413:
            raise ProviderFileTooLargeError(
                "Gemini rejected the file size (HTTP 413)", provider=GEMINI
            )
        if 500 <= code < 600:
            raise ProviderUnavailableError(
                f"Gemini server error (HTTP {code})", provider=GEMINI
            )
        raise ProviderResponseError(
            f"Gemini returned unexpected status {code}", provider=GEMINI
        )

    def _parse(self, response) -> dict[str, Any]:
        try:
            raw = response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                "Gemini returned invalid JSON", provider=GEMINI
            ) from exc
        if not isinstance(raw, dict):
            raise ProviderResponseError(
                "Gemini returned an unexpected JSON payload", provider=GEMINI
            )
        return raw


def _extract_text(raw: dict[str, Any]) -> str:
    try:
        parts = raw["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(parts, list):  # malformed but structurally-valid responses
        return ""
    chunks = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    return "\n".join(chunk.strip() for chunk in chunks if chunk).strip()


def _extract_file_uri(raw: dict[str, Any]) -> str | None:
    file_obj = raw.get("file") if isinstance(raw, dict) else None
    if isinstance(file_obj, dict):
        return file_obj.get("uri") or file_obj.get("name")
    return raw.get("uri") if isinstance(raw, dict) else None
