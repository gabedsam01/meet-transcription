from __future__ import annotations

from pathlib import Path
from typing import Any

from app.diarization.config import DiarizationConfig
from app.diarization.provider import (
    DiarizationProbes,
    DiarizationProvider,
    DiarizationStatus,
    SpeakerTurn,
    build_diarization_provider,
    get_diarization_status,
)


def assign_speakers(
    segments: list[dict[str, Any]], turns: list[SpeakerTurn]
) -> list[dict[str, Any]]:
    """Return NEW segments, each tagged with the speaker of maximally overlapping turn.

    For every segment, the speaker is the label of the turn with the MAX temporal
    overlap with ``[segment.start, segment.end]``. If no turn overlaps (overlap is
    strictly positive), the segment's ``speaker`` stays None. Pure and
    deterministic: on a tie the first turn (in list order) wins; the input is never
    mutated.
    """
    result: list[dict[str, Any]] = []
    for seg in segments:
        new_seg = dict(seg)
        new_seg["speaker"] = _best_speaker(
            _as_float(seg.get("start")), _as_float(seg.get("end")), turns
        )
        result.append(new_seg)
    return result


def _best_speaker(
    seg_start: float, seg_end: float, turns: list[SpeakerTurn]
) -> str | None:
    best_label: str | None = None
    best_overlap = 0.0
    for turn in turns:
        overlap = _overlap(seg_start, seg_end, turn.start, turn.end)
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = turn.speaker
    return best_label


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def diarize_and_align(
    config: DiarizationConfig,
    audio_path: str | Path,
    segments: list[dict[str, Any]],
    *,
    provider: DiarizationProvider | None = None,
    probes: DiarizationProbes | None = None,
) -> tuple[list[dict[str, Any]], DiarizationStatus]:
    """Diarize ``audio_path`` and assign speakers to ``segments``.

    Returns ``(segments, status)``. When diarization is disabled or invalid the
    segments are returned unchanged and the provider is never invoked. This is the
    single helper the worker calls.
    """
    status = get_diarization_status(config, probes=probes)
    if not status.valid:
        return segments, status

    provider = provider or build_diarization_provider(config)
    turns = provider.diarize(
        audio_path,
        min_speakers=config.min_speakers,
        max_speakers=config.max_speakers,
    )
    return assign_speakers(segments, turns), status
