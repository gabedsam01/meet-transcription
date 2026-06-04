from __future__ import annotations

from pathlib import Path

from app.processor import format_transcript
from app.transcription.normalizer import normalize_deepgram
from app.transcription.provider import TranscriptionResult


class DeepgramProvider:
    """Adapts the existing :class:`~app.deepgram_client.DeepgramClient` to the
    provider interface.

    ``text`` is produced by the legacy ``format_transcript`` so the TXT download is
    byte-for-byte unchanged; ``payload`` is the normalized schema with the full raw
    Deepgram response preserved under ``raw``.
    """

    def __init__(self, client, *, model: str, language: str) -> None:
        self._client = client
        self._model = model
        self._language = language

    def transcribe(
        self, source_path: str | Path, *, original_name: str, file_id: str
    ) -> TranscriptionResult:
        raw = self._client.transcribe(source_path)
        text = format_transcript(raw, original_name or "", file_id or "")
        payload = normalize_deepgram(raw, model=self._model, language=self._language)
        return TranscriptionResult(text=text, payload=payload)
