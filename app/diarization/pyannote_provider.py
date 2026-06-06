from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from app.diarization.config import DiarizationConfig
from app.diarization.errors import DiarizationModelError
from app.diarization.provider import SpeakerTurn

LOGGER = logging.getLogger(__name__)


class PyannoteDiarizationProvider:
    """CPU-only speaker diarization backed by ``pyannote.audio``.

    The heavy pipeline is imported and loaded LAZILY (inside ``diarize``), so
    importing this module never requires ``pyannote.audio`` / ``torch`` (optional,
    build-arg-gated deps). ``pipeline_factory`` is injected in tests so no real
    model, network, or token is needed.

    SECURITY: the auth token is passed to the pipeline factory only; it is NEVER
    logged nor included in any raised error message / user_message.
    """

    def __init__(
        self,
        config: DiarizationConfig,
        *,
        pipeline_factory: Callable[[], object] | None = None,
    ) -> None:
        self._config = config
        self._pipeline_factory = pipeline_factory
        self._pipeline: object | None = None

    def _get_pipeline(self):
        if self._pipeline is None:
            factory = self._pipeline_factory or self._build_pipeline
            try:
                self._pipeline = factory()
            except Exception as exc:  # noqa: BLE001 - mapped to a friendly error
                # Do NOT include the token or traceback in the user-facing message.
                raise DiarizationModelError(
                    f"failed to load diarization pipeline {self._config.model!r}: "
                    f"{type(exc).__name__}"
                ) from exc
        return self._pipeline

    def _build_pipeline(self):
        from pyannote.audio import Pipeline  # lazy: optional heavy dependency

        return Pipeline.from_pretrained(
            self._config.model, use_auth_token=self._config.auth_token
        )

    def diarize(
        self,
        audio_path: str | Path,
        *,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> list[SpeakerTurn]:
        pipeline = self._get_pipeline()
        kwargs: dict[str, int] = {}
        if min_speakers is not None:
            kwargs["min_speakers"] = min_speakers
        if max_speakers is not None:
            kwargs["max_speakers"] = max_speakers
        try:
            annotation = pipeline(str(audio_path), **kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a friendly error
            raise DiarizationModelError(
                f"diarization pipeline run failed: {type(exc).__name__}"
            ) from exc
        return _to_turns(annotation)


def _to_turns(annotation) -> list[SpeakerTurn]:
    turns: list[SpeakerTurn] = []
    for turn, _track, label in annotation.itertracks(yield_label=True):
        turns.append(SpeakerTurn(float(turn.start), float(turn.end), str(label)))
    return turns
