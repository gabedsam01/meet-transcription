from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DeepgramError(RuntimeError):
    pass


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

    def transcribe(self, video_path: str | Path) -> dict[str, Any]:
        path = Path(video_path)
        with path.open("rb") as video_file:
            response = self.session.post(
                self.endpoint,
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "video/mp4",
                },
                params=self._params(),
                data=video_file,
                timeout=self.timeout,
            )

        if not 200 <= response.status_code < 300:
            raise DeepgramError(
                f"Deepgram request failed with status {response.status_code}: "
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
