from app.transcription.normalizer import (
    normalize_deepgram,
    normalized_payload,
    render_local_text,
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
