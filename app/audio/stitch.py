from __future__ import annotations

from typing import Any

# Re-assemble per-chunk transcripts (each with times RELATIVE to its chunk) into
# one global transcript. Each segment is shifted to global time by its chunk's
# ``start_offset``; because chunks overlap (see app.audio.chunking.plan_chunks), a
# segment that falls inside the previous chunk's overlap region AND duplicates an
# already-kept segment's text is dropped, so the overlap is not transcribed twice.

_EPSILON = 0.05


def stitch_transcript_chunks(chunks: list[dict]) -> dict:
    """Merge per-chunk transcript dicts into one global transcript.

    Each chunk dict is ``{"start_offset": float, "segments": [...], "text": str}``
    where every segment is the normalizer shape
    ``{"start", "end", "speaker", "text"}`` with times relative to that chunk.
    Chunks are merged in the order given (the caller orders them). Returns
    ``{"text": <joined non-empty segment texts>, "segments": <merged list>}``.
    """

    merged: list[dict[str, Any]] = []
    last_kept_end = 0.0

    for chunk in chunks:
        offset = _float(chunk.get("start_offset"), 0.0)
        for seg in chunk.get("segments") or []:
            global_start = _float(seg.get("start"), 0.0) + offset
            global_end = _float(seg.get("end"), 0.0) + offset
            text = (seg.get("text") or "").strip()

            # Inside the previous chunk's tail (overlap) AND a textual duplicate
            # of something we already kept -> drop it to dedupe the overlap.
            if global_start < last_kept_end - _EPSILON and _is_duplicate(
                merged, text
            ):
                continue

            merged.append(
                {
                    "start": round(global_start, 3),
                    "end": round(global_end, 3),
                    "speaker": seg.get("speaker"),
                    "text": text,
                }
            )
            if global_end > last_kept_end:
                last_kept_end = global_end

    text = " ".join(s["text"] for s in merged if s["text"]).strip()
    return {"text": text, "segments": merged}


def _is_duplicate(merged: list[dict[str, Any]], text: str) -> bool:
    if not text:
        # An empty segment in the overlap carries no content; treat as a dropable
        # duplicate so it never bloats the merged list.
        return True
    # Compare against recently kept segments (the overlap is short, so the
    # duplicate is necessarily near the tail).
    for prev in reversed(merged[-5:]):
        if prev["text"] == text:
            return True
    return False


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
