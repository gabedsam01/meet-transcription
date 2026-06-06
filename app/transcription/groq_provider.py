"""Groq transcription provider.

Posts the media to Groq's audio-transcriptions endpoint and normalizes verbose_json.
HTTP failures map to the friendly ProviderError family.
"""

from __future__ import annotations

import logging
import os
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
from app.transcription.normalizer import normalize_groq, render_transcript_text
from app.transcription.provider import TranscriptionResult
from app.transcription.provider_models import GROQ

LOGGER = logging.getLogger(__name__)


class GroqProvider:
    ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        language: str | None = None,
        session: Any | None = None,
        timeout: int = 600,
        max_file_bytes: int | None = None,
        endpoint: str | None = None,
    ) -> None:
        if not (api_key or "").strip():
            api_key = os.environ.get("GROQ_API_KEY", "")

        if not (api_key or "").strip():
            raise ProviderCredentialMissingError(
                "Groq API key is required", provider=GROQ
            )
        self._api_key = api_key
        self._model = model
        self._language = language
        self._timeout = timeout
        self._endpoint = endpoint or self.ENDPOINT

        if max_file_bytes is None:
            from app.transcription.provider_models import get_provider_spec
            spec = get_provider_spec(GROQ)
            self._max_file_bytes = spec.max_file_bytes if spec else 25 * 1024 * 1024
        else:
            self._max_file_bytes = max_file_bytes

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
        payload = normalize_groq(raw, model=self._model, language=self._language)
        text = render_transcript_text(payload, original_name or "", file_id or "")
        return TranscriptionResult(text=text, payload=payload)

    # -- internals -----------------------------------------------------------

    def _check_size(self, path: Path) -> None:
        size = path.stat().st_size if path.exists() else 0
        if self._max_file_bytes and size > self._max_file_bytes:
            raise ProviderFileTooLargeError(
                f"Groq file is {size} bytes, exceeds {self._max_file_bytes}",
                provider=GROQ,
            )

    def _post(self, path: Path):
        response_format = os.environ.get("GROQ_RESPONSE_FORMAT", "verbose_json")
        granularities_raw = os.environ.get("GROQ_TIMESTAMP_GRANULARITIES", "segment,word")
        granularities = [g.strip() for g in granularities_raw.split(",") if g.strip()]

        data = [
            ("model", self._model),
            ("response_format", response_format),
        ]
        if self._language and self._language not in ("auto", ""):
            data.append(("language", self._language))
        for g in granularities:
            data.append(("timestamp_granularities[]", g))

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
                f"Groq request failed: {exc}", provider=GROQ
            ) from exc

    def _raise_for_status(self, response) -> None:
        code = getattr(response, "status_code", 0)
        if 200 <= code < 300:
            return
        if code in (401, 403):
            raise ProviderCredentialInvalidError(
                f"Groq auth failed (HTTP {code})", provider=GROQ
            )
        if code == 429:
            err = ProviderRateLimitedError(
                "Groq rate limited (HTTP 429)", provider=GROQ
            )
            # Respect retry-after header
            headers = getattr(response, "headers", None) or {}
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if retry_after:
                try:
                    err.retry_after_seconds = int(float(retry_after))
                except ValueError:
                    pass
            raise err
        if code == 413:
            raise ProviderFileTooLargeError(
                "Groq rejected the file size (HTTP 413)", provider=GROQ
            )
        if 500 <= code < 600:
            raise ProviderUnavailableError(
                f"Groq server error (HTTP {code})", provider=GROQ
            )
        raise ProviderResponseError(
            f"Groq returned unexpected status {code}", provider=GROQ
        )

    def _parse(self, response) -> dict[str, Any]:
        try:
            raw = response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                "Groq returned invalid JSON", provider=GROQ
            ) from exc
        if not isinstance(raw, dict):
            raise ProviderResponseError(
                "Groq returned an unexpected JSON payload", provider=GROQ
            )
        return raw
