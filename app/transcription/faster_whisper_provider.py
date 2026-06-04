from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from app.transcription.config import TranscriptionConfig
from app.transcription.normalizer import (
    normalized_payload,
    render_local_text,
    segment,
    segments_text,
)
from app.transcription.provider import TranscriptionResult

LOGGER = logging.getLogger(__name__)


class FasterWhisperProvider:
    """CPU-only faster-whisper provider.

    The heavy ``WhisperModel`` is imported lazily and built once on first use, so
    importing this module never requires the ``faster_whisper`` package (it is an
    optional, build-arg-gated dependency). ``model_factory`` is injected in tests.
    """

    def __init__(
        self,
        config: TranscriptionConfig,
        *,
        model_factory: Callable[[], object] | None = None,
    ) -> None:
        self._config = config
        self._model_factory = model_factory
        self._model: object | None = None

    def _get_model(self):
        if self._model is None:
            factory = self._model_factory or self._build_model
            self._model = factory()
        return self._model

    def _build_model(self):
        from faster_whisper import WhisperModel  # lazy: optional heavy dependency

        return WhisperModel(
            self._config.model,
            device="cpu",
            compute_type=self._config.compute_type,
            cpu_threads=self._config.threads,
            download_root=self._config.model_dir,
            # Never reach out to HuggingFace at runtime unless auto-download is on.
            local_files_only=not self._config.auto_download,
        )

    def transcribe(
        self, source_path: str | Path, *, original_name: str, file_id: str
    ) -> TranscriptionResult:
        model = self._get_model()
        language = None if self._config.language == "auto" else self._config.language
        raw_segments, info = model.transcribe(
            str(source_path), language=language, beam_size=5, vad_filter=True
        )
        segments = [
            segment(s.start, s.end, s.text)
            for s in raw_segments
            if (s.text or "").strip()
        ]
        detected = getattr(info, "language", None) or self._config.language
        payload = normalized_payload(
            provider="local",
            engine="faster-whisper",
            model=self._config.model,
            language=detected,
            text=segments_text(segments),
            segments=segments,
        )
        text = render_local_text(payload, original_name, file_id)
        return TranscriptionResult(text=text, payload=payload)
