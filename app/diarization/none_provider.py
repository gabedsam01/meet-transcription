from __future__ import annotations

from pathlib import Path


class NoneDiarizationProvider:
    """No-op provider used when diarization is disabled or the engine is "none".

    It satisfies the ``DiarizationProvider`` protocol and always returns an empty
    list of turns, so downstream alignment leaves every segment's speaker None.
    """

    def diarize(
        self,
        audio_path: str | Path,
        *,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> list:
        return []
