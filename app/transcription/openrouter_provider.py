"""OpenRouter transcription provider.

Posts the media to OpenRouter's OpenAI-compatible audio-transcriptions endpoint
and normalizes whatever comes back (segments when the model supplies them, a
single segment when it returns text only). HTTP failures map to the friendly
``ProviderError`` family — never a leaked key, never a traceback in the UI.

The HTTP session is injectable so the worker uses ``requests`` while tests pass a
fake. This provider runs only in the worker; the web layer never calls it.
"""

from __future__ import annotations

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
from app.transcription.normalizer import normalize_openrouter, render_transcript_text
from app.transcription.provider import TranscriptionResult
from app.transcription.provider_models import OPENROUTER, OPENROUTER_MAX_BYTES

LOGGER = logging.getLogger(__name__)


class OpenRouterProvider:
    ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        language: str | None = None,
        session: Any | None = None,
        timeout: int = 600,
        max_file_bytes: int | None = OPENROUTER_MAX_BYTES,
        endpoint: str | None = None,
    ) -> None:
        if not (api_key or "").strip():
            raise ProviderCredentialMissingError(
                "OpenRouter API key is required", provider=OPENROUTER
            )
        self._api_key = api_key
        self._model = model
        self._language = language
        self._timeout = timeout
        self._max_file_bytes = max_file_bytes
        self._endpoint = endpoint or self.ENDPOINT
        if session is None:
            import requests

            session = requests
        self._session = session

    def transcribe(
        self, source_path: str | Path, *, original_name: str, file_id: str
    ) -> TranscriptionResult:
        path = Path(source_path)
        self._check_size(path)
        response = self._post(path)
        self._raise_for_status(response)
        raw = self._parse(response)
        payload = normalize_openrouter(raw, model=self._model, language=self._language)
        text = render_transcript_text(payload, original_name or "", file_id or "")
        return TranscriptionResult(text=text, payload=payload)

    # -- internals -----------------------------------------------------------

    def _check_size(self, path: Path) -> None:
        size = path.stat().st_size if path.exists() else 0
        if self._max_file_bytes and size > self._max_file_bytes:
            raise ProviderFileTooLargeError(
                f"OpenRouter file is {size} bytes, exceeds {self._max_file_bytes}",
                provider=OPENROUTER,
            )

    def _post(self, path: Path):
        data = {"model": self._model}
        if self._language and self._language not in ("auto", ""):
            data["language"] = self._language
        try:
            with path.open("rb") as handle:
                return self._session.post(
                    self._endpoint,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=data,
                    files={"file": (path.name, handle, "application/octet-stream")},
                    timeout=self._timeout,
                )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - network failures degrade gracefully
            raise ProviderUnavailableError(
                f"OpenRouter request failed: {exc}", provider=OPENROUTER
            ) from exc

    def _raise_for_status(self, response) -> None:
        code = getattr(response, "status_code", 0)
        if 200 <= code < 300:
            return
        # Only the status code goes into the technical message — never the body,
        # which could echo request material.
        if code in (401, 403):
            raise ProviderCredentialInvalidError(
                f"OpenRouter auth failed (HTTP {code})", provider=OPENROUTER
            )
        if code == 429:
            raise ProviderRateLimitedError(
                "OpenRouter rate limited (HTTP 429)", provider=OPENROUTER
            )
        if code == 413:
            raise ProviderFileTooLargeError(
                "OpenRouter rejected the file size (HTTP 413)", provider=OPENROUTER
            )
        if 500 <= code < 600:
            raise ProviderUnavailableError(
                f"OpenRouter server error (HTTP {code})", provider=OPENROUTER
            )
        raise ProviderResponseError(
            f"OpenRouter returned unexpected status {code}", provider=OPENROUTER
        )

    def _parse(self, response) -> dict[str, Any]:
        try:
            raw = response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                "OpenRouter returned invalid JSON", provider=OPENROUTER
            ) from exc
        if not isinstance(raw, dict):
            raise ProviderResponseError(
                "OpenRouter returned an unexpected JSON payload", provider=OPENROUTER
            )
        return raw
