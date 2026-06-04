from __future__ import annotations

from datetime import datetime
from typing import Any

# Single internal schema shared by Deepgram and local engines. Stored verbatim in
# ``transcripts.transcript_json`` (JSONB).


def segment(
    start: float, end: float, text: str, speaker: Any | None = None
) -> dict[str, Any]:
    return {
        "start": round(float(start or 0), 3),
        "end": round(float(end or 0), 3),
        "speaker": speaker,
        "text": (text or "").strip(),
    }


def segments_text(segments: list[dict[str, Any]]) -> str:
    return " ".join(s["text"] for s in segments if s.get("text")).strip()


def normalized_payload(
    *,
    provider: str,
    engine: str,
    model: str,
    language: str | None,
    text: str,
    segments: list[dict[str, Any]],
    words: list | None = None,
    utterances: list | None = None,
    raw: dict | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "engine": engine,
        "model": model,
        "language": language,
        "text": text,
        "segments": segments,
        "words": words or [],
        "utterances": utterances if utterances is not None else segments,
        "raw": raw if raw is not None else {},
    }


def normalize_deepgram(
    raw: dict[str, Any], *, model: str, language: str | None
) -> dict[str, Any]:
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    utterances = results.get("utterances") or []
    segments = [
        segment(
            u.get("start", 0),
            u.get("end", 0),
            u.get("transcript") or "",
            speaker=u.get("speaker"),
        )
        for u in utterances
        if (u.get("transcript") or "").strip()
    ]
    if segments:
        text = segments_text(segments)
    else:
        text = _deepgram_plain(results)
        if text:
            segments = [segment(0.0, 0.0, text)]
    return normalized_payload(
        provider="deepgram",
        engine="deepgram",
        model=model,
        language=language,
        text=text,
        segments=segments,
        raw=raw,
    )


def render_local_text(
    payload: dict[str, Any], original_name: str, file_id: str
) -> str:
    """Human-readable .txt for a local transcript (download + optional Drive copy).

    Mirrors the Deepgram .txt header so the download experience is consistent.
    """
    processed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    engine = payload.get("engine", "local")
    model = payload.get("model", "")
    lines = [
        "TRANSCRIÇÃO DA REUNIÃO",
        "",
        f"Arquivo original: {original_name}",
        f"Data de processamento: {processed_at}",
        f"ID Google Drive: {file_id}",
        f"Motor: {engine} {model}".rstrip(),
        "",
        "==================================================",
        "",
    ]
    segments = payload.get("segments") or []
    if segments:
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            stamp = _format_seconds(seg.get("start", 0))
            speaker = seg.get("speaker")
            if speaker is not None:
                lines.extend([f"[{stamp}] Speaker {speaker}:", text, ""])
            else:
                lines.extend([f"[{stamp}] {text}", ""])
    else:
        lines.extend([payload.get("text") or "Transcrição não disponível.", ""])
    lines.extend(["==================================================", "", "Fim da transcrição."])
    return "\n".join(lines) + "\n"


def _deepgram_plain(results: dict[str, Any]) -> str:
    try:
        return (
            results["channels"][0]["alternatives"][0]["transcript"] or ""
        ).strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _format_seconds(seconds: Any) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
