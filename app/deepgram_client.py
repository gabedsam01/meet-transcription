from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.errors import (
    DeepgramRateLimitError,
    FileTooLargeError,
    ProviderKeyInvalidError,
)


class DeepgramError(RuntimeError):
    pass


def _retry_after(response) -> int | None:
    """Parse a ``Retry-After`` header (seconds) into an int, or None when absent."""
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


@dataclass
class DeepgramClient:
    api_key: str
    model: str
    language: str
    smart_format: bool
    punctuate: bool
    diarize: bool
    utterances: bool
    session: Any | None = None
    timeout: int = 600
    endpoint: str = "https://api.deepgram.com/v1/listen"

    def __post_init__(self) -> None:
        if self.session is None:
            import requests

            self.session = requests

    @classmethod
    def from_settings(cls, settings) -> "DeepgramClient":
        return cls(
            api_key=settings.deepgram_api_key,
            model=settings.deepgram_model,
            language=settings.deepgram_language,
            smart_format=settings.deepgram_smart_format,
            punctuate=settings.deepgram_punctuate,
            diarize=settings.deepgram_diarize,
            utterances=settings.deepgram_utterances,
            session=getattr(settings, "session", None),
        )

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        model: str = "nova-3",
        language: str = "pt-BR",
        smart_format: bool = True,
        punctuate: bool = True,
        diarize: bool = True,
        utterances: bool = True,
        session: Any | None = None,
    ) -> "DeepgramClient":
        return cls(
            api_key=api_key, model=model, language=language,
            smart_format=smart_format, punctuate=punctuate, diarize=diarize,
            utterances=utterances, session=session,
        )

    def transcribe(self, video_path: str | Path, api_key: str | None = None) -> dict[str, Any]:
        key = api_key or self.api_key
        if not key:
            raise DeepgramError("Deepgram API key is required")
        path = Path(video_path)
        with path.open("rb") as video_file:
            response = self.session.post(
                self.endpoint,
                headers={
                    "Authorization": f"Token {key}",
                    "Content-Type": "video/mp4",
                },
                params=self._params(),
                data=video_file,
                timeout=self.timeout,
            )

        status = response.status_code
        # Map the HTTP failures the retry policy cares about to typed errors:
        # 429 is transient (retry with backoff, honoring Retry-After); 401/403
        # (bad key) and 413 (file too large) are terminal — never retry.
        if status == 429:
            raise DeepgramRateLimitError(
                f"Deepgram rate limited (status {status})",
                retry_after_seconds=_retry_after(response),
            )
        if status in (401, 403):
            raise ProviderKeyInvalidError(f"Deepgram auth failed (status {status})")
        if status == 413:
            raise FileTooLargeError(f"Deepgram rejected the file (status {status})")
        if not 200 <= status < 300:
            raise DeepgramError(
                f"Deepgram request failed with status {status}: "
                f"{response.text}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise DeepgramError("Deepgram returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DeepgramError("Deepgram returned an unexpected JSON payload")
        return payload

    def _params(self) -> dict[str, str]:
        return {
            "model": self.model,
            "language": self.language,
            "smart_format": _bool_param(self.smart_format),
            "punctuate": _bool_param(self.punctuate),
            "diarize": _bool_param(self.diarize),
            "utterances": _bool_param(self.utterances),
        }


def _bool_param(value: bool) -> str:
    return "true" if value else "false"
