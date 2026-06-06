from __future__ import annotations

import pytest

from app.audio.chunking import (
    AudioChunk,
    build_chunk_command,
    chunk_audio,
    plan_chunks,
)


class _FakeResult:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


# --- plan_chunks ------------------------------------------------------------


def test_plan_chunks_single_window_when_under_max():
    windows = plan_chunks(120.0, max_duration_seconds=900, overlap_seconds=2)
    assert windows == [(0.0, 120.0)]


def test_plan_chunks_single_window_when_equal_max():
    windows = plan_chunks(900.0, max_duration_seconds=900, overlap_seconds=2)
    assert windows == [(0.0, 900.0)]


def test_plan_chunks_multiple_windows_with_overlap():
    windows = plan_chunks(2000.0, max_duration_seconds=900, overlap_seconds=2)
    # first window: 0..900
    assert windows[0] == (0.0, 900.0)
    # each next window starts at prev_end - overlap
    for prev, nxt in zip(windows, windows[1:]):
        assert nxt[0] == pytest.approx(prev[1] - 2)
    # last window ends exactly at duration
    assert windows[-1][1] == pytest.approx(2000.0)
    # windows cover the whole range
    assert windows[0][0] == 0.0


def test_plan_chunks_overlap_must_be_less_than_max():
    # overlap >= max would not make progress; guarded -> overlap is clamped
    windows = plan_chunks(2000.0, max_duration_seconds=900, overlap_seconds=900)
    # still terminates and reaches the end
    assert windows[-1][1] == pytest.approx(2000.0)
    assert len(windows) >= 2


def test_plan_chunks_zero_duration():
    assert plan_chunks(0.0, max_duration_seconds=900, overlap_seconds=2) == [(0.0, 0.0)]


# --- build_chunk_command ----------------------------------------------------


def test_build_chunk_command_shape():
    cmd = build_chunk_command("in.wav", "chunk0.wav", 10.0, 30.0)
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "10.0"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "30.0"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "in.wav"
    assert cmd[-1] == "chunk0.wav"


# --- chunk_audio ------------------------------------------------------------


def test_chunk_audio_single_chunk(tmp_path):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return _FakeResult(returncode=0)

    out = tmp_path / "chunks"
    chunks = chunk_audio(
        str(tmp_path / "audio.wav"),
        str(out),
        max_duration_seconds=900,
        overlap_seconds=2,
        duration_seconds=120.0,
        runner=runner,
    )
    assert len(chunks) == 1
    assert isinstance(chunks[0], AudioChunk)
    assert chunks[0].index == 0
    assert chunks[0].start_seconds == 0.0
    assert chunks[0].end_seconds == pytest.approx(120.0)
    assert out.exists()
    assert len(calls) == 1


def test_chunk_audio_multiple_chunks_offsets(tmp_path):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return _FakeResult(returncode=0)

    out = tmp_path / "chunks"
    chunks = chunk_audio(
        str(tmp_path / "audio.wav"),
        str(out),
        max_duration_seconds=900,
        overlap_seconds=2,
        duration_seconds=2000.0,
        runner=runner,
    )
    assert len(chunks) >= 2
    # indices are sequential
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # offsets match the plan
    plan = plan_chunks(2000.0, 900, 2)
    for chunk, (start, end) in zip(chunks, plan):
        assert chunk.start_seconds == pytest.approx(start)
        assert chunk.end_seconds == pytest.approx(end)
    # one ffmpeg call per chunk
    assert len(calls) == len(chunks)
    # last chunk reaches the end
    assert chunks[-1].end_seconds == pytest.approx(2000.0)


def test_chunk_audio_probes_when_duration_none(tmp_path):
    import json

    def runner(cmd):
        if cmd[0] == "ffprobe":
            payload = json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "audio",
                            "codec_name": "pcm_s16le",
                            "sample_rate": "16000",
                            "channels": 1,
                        }
                    ],
                    "format": {"duration": "100.0"},
                }
            )

            class _R:
                returncode = 0
                stdout = payload
                stderr = ""

            return _R()

        class _R2:
            returncode = 0
            stderr = ""

        return _R2()

    out = tmp_path / "chunks"
    chunks = chunk_audio(
        str(tmp_path / "audio.wav"),
        str(out),
        max_duration_seconds=900,
        overlap_seconds=2,
        duration_seconds=None,
        runner=runner,
    )
    assert len(chunks) == 1
    assert chunks[0].end_seconds == pytest.approx(100.0)
