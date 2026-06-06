from __future__ import annotations

import pytest

from app.audio.stitch import stitch_transcript_chunks


def _seg(start, end, text, speaker=None):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


def test_stitch_single_chunk_passthrough():
    chunks = [
        {
            "start_offset": 0.0,
            "text": "ola mundo",
            "segments": [_seg(0.0, 1.0, "ola"), _seg(1.0, 2.0, "mundo")],
        }
    ]
    result = stitch_transcript_chunks(chunks)
    assert result["text"] == "ola mundo"
    assert [s["text"] for s in result["segments"]] == ["ola", "mundo"]
    # times unchanged for offset 0
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][1]["start"] == 1.0


def test_stitch_applies_start_offset_to_segment_times():
    chunks = [
        {
            "start_offset": 0.0,
            "text": "primeiro",
            "segments": [_seg(0.0, 5.0, "primeiro")],
        },
        {
            "start_offset": 100.0,
            "text": "segundo",
            "segments": [_seg(2.0, 4.0, "segundo")],
        },
    ]
    result = stitch_transcript_chunks(chunks)
    # second chunk segment shifted by 100
    second = result["segments"][-1]
    assert second["start"] == pytest.approx(102.0)
    assert second["end"] == pytest.approx(104.0)
    assert second["text"] == "segundo"
    assert result["text"] == "primeiro segundo"


def test_stitch_dedupes_overlap_region():
    # chunk0 covers 0..10, chunk1 starts at offset 8 (2s overlap).
    # chunk1's first segment is a duplicate of chunk0's last segment.
    chunks = [
        {
            "start_offset": 0.0,
            "text": "a b",
            "segments": [_seg(0.0, 4.0, "a"), _seg(8.5, 9.5, "b")],
        },
        {
            "start_offset": 8.0,
            "text": "b c",
            # first segment (relative 0.5..1.5 -> global 8.5..9.5) duplicates "b"
            "segments": [_seg(0.5, 1.5, "b"), _seg(3.0, 5.0, "c")],
        },
    ]
    result = stitch_transcript_chunks(chunks)
    texts = [s["text"] for s in result["segments"]]
    # "b" must appear only once
    assert texts.count("b") == 1
    assert texts == ["a", "b", "c"]
    assert result["text"] == "a b c"


def test_stitch_keeps_non_duplicate_in_overlap():
    # A segment in the overlap window that is NOT a textual duplicate is kept.
    chunks = [
        {
            "start_offset": 0.0,
            "text": "x",
            "segments": [_seg(8.5, 9.5, "x")],
        },
        {
            "start_offset": 8.0,
            "text": "y",
            "segments": [_seg(0.5, 1.5, "y")],
        },
    ]
    result = stitch_transcript_chunks(chunks)
    texts = [s["text"] for s in result["segments"]]
    assert texts == ["x", "y"]


def test_stitch_empty_segments_skipped_in_text():
    chunks = [
        {
            "start_offset": 0.0,
            "text": "",
            "segments": [_seg(0.0, 1.0, ""), _seg(1.0, 2.0, "ola")],
        }
    ]
    result = stitch_transcript_chunks(chunks)
    assert result["text"] == "ola"


def test_stitch_empty_input():
    result = stitch_transcript_chunks([])
    assert result == {"text": "", "segments": []}


def test_stitch_preserves_speaker_and_chunk_order():
    chunks = [
        {
            "start_offset": 50.0,
            "text": "world",
            "segments": [_seg(1.0, 2.0, "world", speaker=1)],
        },
        {
            "start_offset": 0.0,
            "text": "hello",
            "segments": [_seg(0.0, 1.0, "hello", speaker=0)],
        },
    ]
    # chunks are processed in list order (caller is responsible for ordering)
    result = stitch_transcript_chunks(chunks)
    assert [s["text"] for s in result["segments"]] == ["world", "hello"]
    assert result["segments"][0]["speaker"] == 1
    assert result["segments"][0]["start"] == pytest.approx(51.0)
