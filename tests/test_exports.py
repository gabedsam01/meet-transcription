import json

import pytest

from app.exports import (
    EXPORT_FORMATS,
    PLANNED_FORMATS,
    available_formats,
    build_export,
    is_supported,
    to_json,
    to_markdown,
    to_srt,
    to_vtt,
)

PAYLOAD = {
    "provider": "deepgram",
    "engine": "deepgram",
    "model": "nova-3",
    "language": "pt-BR",
    "text": "Olá mundo. Tudo bem?",
    "segments": [
        {"start": 0.0, "end": 2.5, "speaker": 0, "text": "Olá mundo."},
        {"start": 2.5, "end": 5.0, "speaker": 1, "text": "Tudo bem?"},
    ],
    "words": [],
    "utterances": [],
    "raw": {},
}


def test_supported_and_planned_formats():
    assert set(EXPORT_FORMATS) == {"txt", "json", "srt", "vtt", "md"}
    assert is_supported("srt") and not is_supported("pdf")
    assert "pdf" in PLANNED_FORMATS
    assert dict(available_formats())["txt"].startswith("Texto")


def test_txt_export_is_the_stored_text_verbatim():
    export = build_export("txt", transcript_text="BODY", payload=PAYLOAD, base_name="meet")
    assert export.content == "BODY"
    assert export.filename == "meet_Transcricao.txt"
    assert export.media_type.startswith("text/plain")


def test_json_export_roundtrips_payload():
    export = build_export("json", transcript_text="", payload=PAYLOAD, base_name="meet")
    data = json.loads(export.content)
    assert data["provider"] == "deepgram"
    assert export.filename.endswith(".json")


def test_srt_has_indices_and_comma_millis():
    out = to_srt(PAYLOAD)
    assert "1\n00:00:00,000 --> 00:00:02,500" in out
    assert "2\n00:00:02,500 --> 00:00:05,000" in out
    assert "Olá mundo." in out


def test_vtt_has_header_and_dot_millis():
    out = to_vtt(PAYLOAD)
    assert out.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.500" in out


def test_markdown_renders_title_and_speakers():
    out = to_markdown(PAYLOAD, title="Reunião semanal")
    assert out.startswith("# Reunião semanal")
    assert "Speaker 0" in out and "Tudo bem?" in out


def test_subtitle_exports_fall_back_to_full_text_without_segments():
    payload = {"text": "corpo único", "segments": []}
    assert "corpo único" in to_srt(payload, "corpo único")
    assert "corpo único" in to_vtt(payload, "corpo único")
    assert "corpo único" in to_markdown(payload, "corpo único")


def test_json_export_uses_text_when_payload_empty():
    data = json.loads(to_json({}, "apenas o texto"))
    assert data == {"text": "apenas o texto"}


def test_build_export_rejects_unknown_format():
    with pytest.raises(ValueError):
        build_export("pdf", transcript_text="x", payload=PAYLOAD, base_name="m")
