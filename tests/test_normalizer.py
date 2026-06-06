from app.transcription.normalizer import (
    normalize_deepgram,
    normalize_gemini,
    normalize_openrouter,
    normalized_payload,
    render_local_text,
    render_transcript_text,
    segment,
    segments_text,
)


def test_normalized_payload_has_canonical_schema_keys():
    payload = normalized_payload(
        provider="local",
        engine="faster-whisper",
        model="small",
        language="pt",
        text="ola",
        segments=[segment(0.0, 1.0, "ola")],
    )
    assert set(payload) == {
        "provider",
        "engine",
        "model",
        "language",
        "text",
        "segments",
        "words",
        "utterances",
        "raw",
    }
    assert payload["segments"][0]["speaker"] is None
    assert payload["utterances"] == payload["segments"]
    assert payload["words"] == []


def test_segments_text_joins_non_empty():
    segs = [segment(0, 1, "Olá"), segment(1, 2, ""), segment(2, 3, "mundo")]
    assert segments_text(segs) == "Olá mundo"


def test_normalize_deepgram_maps_utterances_to_segments_and_keeps_raw():
    raw = {
        "results": {
            "utterances": [
                {"start": 1.0, "end": 2.5, "speaker": 0, "transcript": "Ola mundo."}
            ]
        }
    }
    payload = normalize_deepgram(raw, model="nova-3", language="pt-BR")
    assert payload["provider"] == "deepgram"
    assert payload["engine"] == "deepgram"
    assert payload["raw"] == raw  # full upstream response preserved
    assert payload["segments"][0]["text"] == "Ola mundo."
    assert payload["segments"][0]["speaker"] == 0
    assert payload["text"] == "Ola mundo."


def test_normalize_deepgram_without_utterances_uses_plain_transcript():
    raw = {
        "results": {
            "channels": [
                {"alternatives": [{"transcript": "plain text here"}]}
            ]
        }
    }
    payload = normalize_deepgram(raw, model="nova-3", language="pt-BR")
    assert payload["text"] == "plain text here"


def test_render_local_text_has_header_and_segment_lines():
    payload = normalized_payload(
        provider="local",
        engine="faster-whisper",
        model="small",
        language="pt",
        text="Olá mundo",
        segments=[segment(0.0, 3.2, "Olá mundo")],
    )
    text = render_local_text(payload, "meeting.mp4", "drive-id-123")
    assert "TRANSCRIÇÃO DA REUNIÃO" in text
    assert "meeting.mp4" in text
    assert "drive-id-123" in text
    assert "Olá mundo" in text
    assert "faster-whisper" in text  # engine/model surfaced in the header


def test_normalize_openrouter_with_segments():
    raw = {
        "text": "Olá mundo",
        "language": "pt",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "Olá"},
            {"start": 1.0, "end": 2.0, "text": "mundo"},
        ],
    }
    payload = normalize_openrouter(raw, model="openai/whisper-large-v3", language="auto")
    assert payload["provider"] == "openrouter"
    assert payload["engine"] == "openrouter"
    assert payload["language"] == "pt"
    assert [s["text"] for s in payload["segments"]] == ["Olá", "mundo"]
    assert payload["segments"][0]["speaker"] is None  # no diarization
    assert payload["raw"] == raw


def test_normalize_openrouter_text_only_creates_single_segment():
    raw = {"text": "apenas texto"}
    payload = normalize_openrouter(raw, model="m", language="pt")
    assert payload["text"] == "apenas texto"
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["text"] == "apenas texto"
    assert payload["language"] == "pt"  # falls back to requested language


def test_normalize_gemini_single_segment_no_diarization():
    payload = normalize_gemini(
        "Speaker 1: olá\nSpeaker 2: tudo bem", model="gemini-2.5-flash", language="pt"
    )
    assert payload["provider"] == "gemini"
    assert payload["engine"] == "gemini"
    assert payload["text"].startswith("Speaker 1: olá")
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["speaker"] is None  # pseudo, never structured


def test_render_transcript_text_is_engine_agnostic_alias():
    assert render_transcript_text is render_local_text
    payload = normalize_openrouter({"text": "oi"}, model="m", language="pt")
    text = render_transcript_text(payload, "meet.mp4", "id-1")
    assert "TRANSCRIÇÃO DA REUNIÃO" in text
    assert "openrouter" in text
