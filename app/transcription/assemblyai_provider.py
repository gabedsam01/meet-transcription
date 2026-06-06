"""AssemblyAI transcription provider.

Uploads media to AssemblyAI, submits a transcription job with speaker diarization,
polls until completion, and normalizes the turn-by-turn utterances.
"""

from __future__ import annotations

import logging
import os
import time
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
from app.transcription.normalizer import normalize_assemblyai, render_transcript_text
from app.transcription.provider import TranscriptionResult
from app.transcription.provider_models import ASSEMBLYAI

LOGGER = logging.getLogger(__name__)


class AssemblyAIProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        speaker_labels: bool | None = None,
        speakers_expected: int | None = None,
        language: str | None = None,
        session: Any | None = None,
        timeout: int = 1800,
        max_file_bytes: int | None = None,
    ) -> None:
        if not (api_key or "").strip():
            api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")

        if not (api_key or "").strip():
            raise ProviderCredentialMissingError(
                "AssemblyAI API key is required", provider=ASSEMBLYAI
            )

        self._api_key = api_key.strip()
        self._model = model
        self._language = language
        self._timeout = timeout

        # Load defaults from environment variables
        env_labels = os.environ.get("ASSEMBLYAI_SPEAKER_LABELS", "true").strip().lower() in ("1", "true", "yes", "on")
        env_expected_raw = os.environ.get("ASSEMBLYAI_SPEAKERS_EXPECTED", "")
        env_expected = int(env_expected_raw.strip()) if env_expected_raw.strip().isdigit() else None

        self._speaker_labels = speaker_labels if speaker_labels is not None else env_labels
        self._speakers_expected = speakers_expected if speakers_expected is not None else env_expected

        if max_file_bytes is None:
            from app.transcription.provider_models import get_provider_spec
            spec = get_provider_spec(ASSEMBLYAI)
            self._max_file_bytes = spec.max_file_bytes if spec else 99 * 1024 * 1024
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

        # 1. Upload
        upload_url = self._upload(path)

        # 2. Submit
        transcript_id = self._submit(upload_url)

        # 3. Poll
        raw = self._poll(transcript_id)

        # 4. Normalize
        payload = normalize_assemblyai(raw, model=self._model, language=self._language)
        text = render_transcript_text(payload, original_name or "", file_id or "")
        return TranscriptionResult(text=text, payload=payload)

    # -- internals -----------------------------------------------------------

    def _check_size(self, path: Path) -> None:
        size = path.stat().st_size if path.exists() else 0
        if self._max_file_bytes and size > self._max_file_bytes:
            raise ProviderFileTooLargeError(
                f"AssemblyAI file is {size} bytes, exceeds {self._max_file_bytes}",
                provider=ASSEMBLYAI,
            )

    def _upload(self, path: Path) -> str:
        headers = {"Authorization": self._api_key}
        try:
            with path.open("rb") as handle:
                response = self._session.post(
                    "https://api.assemblyai.com/v2/upload",
                    headers=headers,
                    data=handle,
                    timeout=300,
                )
            self._raise_for_status(response)
            return response.json()["upload_url"]
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(
                f"AssemblyAI upload failed: {exc}", provider=ASSEMBLYAI
            ) from exc

    def _submit(self, upload_url: str) -> str:
        headers = {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
        }
        data = {
            "audio_url": upload_url,
            "speaker_labels": self._speaker_labels,
        }
        # AssemblyAI speech_model is sent. If universal-3-pro or universal-2 is used,
        # we set speech_model parameter.
        if self._model:
            data["speech_model"] = self._model
        if self._speakers_expected is not None:
            data["speakers_expected"] = self._speakers_expected

        try:
            response = self._session.post(
                "https://api.assemblyai.com/v2/transcript",
                headers=headers,
                json=data,
                timeout=30,
            )
            self._raise_for_status(response)
            return response.json()["id"]
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(
                f"AssemblyAI transcription job submission failed: {exc}", provider=ASSEMBLYAI
            ) from exc

    def _poll(self, transcript_id: str) -> dict[str, Any]:
        headers = {"Authorization": self._api_key}
        url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"

        start_time = time.monotonic()
        poll_interval = int(os.environ.get("ASSEMBLYAI_POLL_INTERVAL_SECONDS", "3"))
        # Protect against local negative/zero configurations causing infinite loops or crash
        if poll_interval <= 0:
            poll_interval = 3

        while True:
            if time.monotonic() - start_time > self._timeout:
                raise ProviderUnavailableError(
                    f"AssemblyAI transcription timed out after {self._timeout} seconds",
                    provider=ASSEMBLYAI,
                )

            try:
                response = self._session.get(url, headers=headers, timeout=30)
                self._raise_for_status(response)
                data = response.json()
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProviderUnavailableError(
                    f"AssemblyAI polling failed: {exc}", provider=ASSEMBLYAI
                ) from exc

            status = data.get("status")
            if status == "completed":
                return data
            if status == "error":
                error_msg = data.get("error") or "Unknown transcription error"
                raise ProviderResponseError(
                    f"AssemblyAI transcription failed: {error_msg}", provider=ASSEMBLYAI
                )

            time.sleep(poll_interval)

    def _raise_for_status(self, response) -> None:
        code = getattr(response, "status_code", 0)
        if 200 <= code < 300:
            return
        if code in (401, 403):
            raise ProviderCredentialInvalidError(
                f"AssemblyAI auth failed (HTTP {code})", provider=ASSEMBLYAI
            )
        if code == 429:
            err = ProviderRateLimitedError(
                "AssemblyAI rate limited (HTTP 429)", provider=ASSEMBLYAI
            )
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
                "AssemblyAI rejected the file size (HTTP 413)", provider=ASSEMBLYAI
            )
        if 500 <= code < 600:
            raise ProviderUnavailableError(
                f"AssemblyAI server error (HTTP {code})", provider=ASSEMBLYAI
            )
        raise ProviderResponseError(
            f"AssemblyAI returned unexpected status {code}", provider=ASSEMBLYAI
        )
