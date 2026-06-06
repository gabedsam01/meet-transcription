from __future__ import annotations

from app.diarization.align import assign_speakers
from app.diarization.provider import SpeakerTurn


def _seg(start, end, text="x", speaker=None):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


def test_assign_speaker_by_max_overlap():
    segments = [_seg(0.0, 2.0)]
    turns = [
        SpeakerTurn(0.0, 0.5, "A"),
        SpeakerTurn(0.5, 2.0, "B"),  # larger overlap
    ]
    out = assign_speakers(segments, turns)
    assert out[0]["speaker"] == "B"


def test_assign_no_overlap_leaves_speaker_none():
    segments = [_seg(10.0, 11.0)]
    turns = [SpeakerTurn(0.0, 2.0, "A")]
    out = assign_speakers(segments, turns)
    assert out[0]["speaker"] is None


def test_assign_returns_new_list_does_not_mutate():
    segments = [_seg(0.0, 1.0)]
    turns = [SpeakerTurn(0.0, 1.0, "A")]
    out = assign_speakers(segments, turns)
    assert out is not segments
    assert out[0] is not segments[0]
    assert segments[0]["speaker"] is None
    assert out[0]["speaker"] == "A"


def test_assign_multiple_segments_each_get_own_speaker():
    segments = [_seg(0.0, 1.0), _seg(5.0, 6.0), _seg(2.5, 3.5)]
    turns = [
        SpeakerTurn(0.0, 1.0, "A"),
        SpeakerTurn(2.0, 4.0, "B"),
        SpeakerTurn(5.0, 7.0, "C"),
    ]
    out = assign_speakers(segments, turns)
    assert [s["speaker"] for s in out] == ["A", "C", "B"]


def test_assign_with_empty_turns_leaves_all_none():
    segments = [_seg(0.0, 1.0), _seg(1.0, 2.0)]
    out = assign_speakers(segments, [])
    assert all(s["speaker"] is None for s in out)


def test_assign_with_empty_segments_returns_empty():
    assert assign_speakers([], [SpeakerTurn(0.0, 1.0, "A")]) == []


def test_assign_preserves_other_fields():
    segments = [_seg(0.0, 1.0, text="hello world")]
    turns = [SpeakerTurn(0.0, 1.0, "A")]
    out = assign_speakers(segments, turns)
    assert out[0]["text"] == "hello world"
    assert out[0]["start"] == 0.0
    assert out[0]["end"] == 1.0


def test_assign_tie_overlap_is_deterministic():
    # Two turns with identical overlap; first in list wins (stable max).
    segments = [_seg(0.0, 2.0)]
    turns = [
        SpeakerTurn(0.0, 1.0, "A"),
        SpeakerTurn(1.0, 2.0, "B"),
    ]
    out = assign_speakers(segments, turns)
    assert out[0]["speaker"] == "A"


def test_assign_partial_overlap_picks_dominant():
    segments = [_seg(1.0, 5.0)]
    turns = [
        SpeakerTurn(0.0, 2.0, "A"),  # overlap 1.0..2.0 = 1.0
        SpeakerTurn(2.0, 6.0, "B"),  # overlap 2.0..5.0 = 3.0
    ]
    out = assign_speakers(segments, turns)
    assert out[0]["speaker"] == "B"


def test_assign_zero_length_segment_no_overlap():
    segments = [_seg(3.0, 3.0)]
    turns = [SpeakerTurn(0.0, 2.0, "A")]
    out = assign_speakers(segments, turns)
    assert out[0]["speaker"] is None
