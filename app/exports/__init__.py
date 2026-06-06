"""Transcript export formats.

Pure functions that turn a stored transcript (the human-readable ``text`` plus the
normalized ``transcript_json`` payload — see ``app/transcription/normalizer.py``)
into downloadable artifacts. The web layer wires these to
``GET /jobs/{id}/download?format=...``; nothing here touches the database, Drive,
or any provider.

Supported now: ``txt``, ``json``, ``srt``, ``vtt``, ``md``. ``pdf`` is documented
as a future format (it needs a heavy rendering dependency) — see
``documentation/36-export-formats.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: format -> (media_type, file extension, human label). Order is UI order.
EXPORT_FORMATS: dict[str, tuple[str, str, str]] = {
    "txt": ("text/plain; charset=utf-8", "txt", "Texto (.txt)"),
    "json": ("application/json; charset=utf-8", "json", "JSON (.json)"),
    "srt": ("application/x-subrip; charset=utf-8", "srt", "Legendas SRT (.srt)"),
    "vtt": ("text/vtt; charset=utf-8", "vtt", "Legendas WebVTT (.vtt)"),
    "md": ("text/markdown; charset=utf-8", "md", "Markdown (.md)"),
}

#: Formats on the roadmap but intentionally not implemented (heavy deps).
PLANNED_FORMATS = ("pdf",)

DEFAULT_FORMAT = "txt"


@dataclass(frozen=True)
class Export:
    filename: str
    content: str
    media_type: str


def is_supported(fmt: str) -> bool:
    return fmt in EXPORT_FORMATS


def available_formats() -> list[tuple[str, str]]:
    """``[(fmt, label), ...]`` for rendering download options in the UI."""
    return [(fmt, meta[2]) for fmt, meta in EXPORT_FORMATS.items()]


def build_export(
    fmt: str,
    *,
    transcript_text: str,
    payload: dict[str, Any] | None,
    base_name: str,
    original_name: str = "",
    file_id: str = "",
) -> Export:
    """Render ``fmt`` for a transcript. Raises ``ValueError`` on an unknown format."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {fmt!r}")
    payload = payload or {}
    media_type, ext, _ = EXPORT_FORMATS[fmt]
    if fmt == "txt":
        content = transcript_text
    elif fmt == "json":
        content = to_json(payload, transcript_text)
    elif fmt == "srt":
        content = to_srt(payload, transcript_text)
    elif fmt == "vtt":
        content = to_vtt(payload, transcript_text)
    else:  # md
        content = to_markdown(payload, transcript_text, title=original_name or base_name)
    return Export(filename=f"{base_name}_Transcricao.{ext}", content=content, media_type=media_type)


# --- renderers --------------------------------------------------------------


def to_json(payload: dict[str, Any], transcript_text: str = "") -> str:
    data = dict(payload) if payload else {"text": transcript_text}
    return json.dumps(data, ensure_ascii=False, indent=2)


def to_srt(payload: dict[str, Any], transcript_text: str = "") -> str:
    cues = _cues(payload, transcript_text)
    blocks = []
    for index, (start, end, text) in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_timestamp(start, ',')} --> {_timestamp(end, ',')}\n{text}\n"
        )
    return "\n".join(blocks)


def to_vtt(payload: dict[str, Any], transcript_text: str = "") -> str:
    cues = _cues(payload, transcript_text)
    blocks = ["WEBVTT", ""]
    for start, end, text in cues:
        blocks.append(f"{_timestamp(start, '.')} --> {_timestamp(end, '.')}\n{text}\n")
    return "\n".join(blocks)


def to_markdown(payload: dict[str, Any], transcript_text: str = "", *, title: str = "") -> str:
    heading = title.strip() or "Transcrição da reunião"
    lines = [f"# {heading}", ""]
    meta = []
    if payload.get("provider"):
        engine = f"{payload.get('engine', '')} {payload.get('model', '')}".strip()
        meta.append(f"- **Motor:** {engine or payload['provider']}")
    if payload.get("language"):
        meta.append(f"- **Idioma:** {payload['language']}")
    if meta:
        lines.extend(meta + [""])
    lines.append("## Transcrição")
    lines.append("")
    segments = payload.get("segments") or []
    rendered = False
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        rendered = True
        stamp = _clock(seg.get("start", 0))
        speaker = seg.get("speaker")
        prefix = f"**[{stamp}] Speaker {speaker}:** " if speaker is not None else f"**[{stamp}]** "
        lines.append(f"{prefix}{text}")
        lines.append("")
    if not rendered:
        lines.append((payload.get("text") or transcript_text or "Transcrição não disponível.").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- helpers ----------------------------------------------------------------


def _cues(payload: dict[str, Any], transcript_text: str) -> list[tuple[float, float, str]]:
    """Subtitle cues ``(start, end, text)`` in seconds from the normalized segments.

    Falls back to a single full-text cue when there are no usable segments, so SRT
    and VTT always produce a valid (if coarse) file.
    """
    cues: list[tuple[float, float, str]] = []
    for seg in payload.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        if end <= start:
            end = start + 2.0  # give zero-length/instant segments a readable window
        speaker = seg.get("speaker")
        if speaker is not None:
            text = f"[Speaker {speaker}] {text}"
        cues.append((start, end, text))
    if not cues:
        body = (payload.get("text") or transcript_text or "").strip()
        if body:
            cues.append((0.0, max(2.0, len(body.split()) * 0.4), body))
    return cues


def _timestamp(seconds: float, millis_sep: str) -> str:
    """``HH:MM:SS,mmm`` (SRT, sep ``,``) or ``HH:MM:SS.mmm`` (VTT, sep ``.``)."""
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = int(total % 60)
    millis = int(round((total - int(total)) * 1000))
    if millis == 1000:  # rounding spilled into the next second
        secs, millis = secs + 1, 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{millis_sep}{millis:03d}"


def _clock(seconds: Any) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


__all__ = [
    "EXPORT_FORMATS",
    "PLANNED_FORMATS",
    "DEFAULT_FORMAT",
    "Export",
    "is_supported",
    "available_formats",
    "build_export",
    "to_json",
    "to_srt",
    "to_vtt",
    "to_markdown",
]
