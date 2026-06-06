from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.audio.errors import FfmpegError
from app.audio.probe import probe_audio


@dataclass(frozen=True)
class AudioChunk:
    index: int
    path: str
    start_seconds: float
    end_seconds: float


def plan_chunks(
    duration_seconds: float, max_duration_seconds: int, overlap_seconds: int
) -> list[tuple[float, float]]:
    """Plan (start, end) windows covering ``[0, duration]``.

    Pure and deterministic. If the whole file fits in one window
    (``duration <= max``) a single ``[(0.0, duration)]`` is returned. Otherwise
    each window is at most ``max_duration_seconds`` long and consecutive windows
    overlap by ``overlap_seconds`` (each next start = prev_end - overlap), so a
    word straddling a cut appears in both chunks; :func:`app.audio.stitch
    .stitch_transcript_chunks` dedupes that overlap afterwards. The final window
    always ends exactly at ``duration``.

    ``overlap_seconds`` is clamped to be strictly less than
    ``max_duration_seconds`` so windows always make forward progress (an overlap
    >= max would never advance).
    """

    duration = max(0.0, float(duration_seconds))
    max_dur = float(max_duration_seconds)
    if max_dur <= 0:
        return [(0.0, duration)]
    if duration <= max_dur:
        return [(0.0, duration)]

    # Guard: overlap must be strictly less than the window length, else the next
    # start never advances past the current one.
    overlap = float(overlap_seconds)
    if overlap < 0:
        overlap = 0.0
    if overlap >= max_dur:
        overlap = max_dur / 2.0

    windows: list[tuple[float, float]] = []
    start = 0.0
    while True:
        end = start + max_dur
        if end >= duration:
            windows.append((start, duration))
            break
        windows.append((start, end))
        start = end - overlap
    return windows


def build_chunk_command(
    source: str | Path,
    dest: str | Path,
    start_seconds: float,
    length_seconds: float,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build the ffmpeg command that copies a ``[start, start+length]`` slice.

    ``-ss`` is placed before ``-i`` (input seeking) for speed, and ``-c copy``
    stream-copies the slice without re-encoding. This assumes the input is the
    already-extracted PCM WAV (from :func:`app.audio.preprocessor.extract_audio`),
    where stream-copy is exact; for compressed inputs a re-encode would be needed.
    """

    return [
        ffmpeg_bin,
        "-nostdin",
        "-y",
        "-ss",
        str(start_seconds),
        "-t",
        str(length_seconds),
        "-i",
        str(source),
        "-c",
        "copy",
        str(dest),
    ]


def chunk_audio(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    max_duration_seconds: int = 900,
    overlap_seconds: int = 2,
    duration_seconds: float | None = None,
    runner: Callable[[list[str]], object] | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> list[AudioChunk]:
    """Split ``input_path`` into overlapping chunks under ``output_dir``.

    When ``duration_seconds`` is ``None`` the duration is discovered by probing
    ``input_path`` (the same ``runner`` is reused for ffprobe and ffmpeg so tests
    need no real binaries). One ffmpeg invocation runs per planned window; on a
    nonzero return code we raise :class:`FfmpegError`.
    """

    runner = runner or _default_runner

    if duration_seconds is None:
        info = probe_audio(input_path, runner=runner)
        duration_seconds = info.duration_seconds

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    windows = plan_chunks(duration_seconds, max_duration_seconds, overlap_seconds)
    chunks: list[AudioChunk] = []
    for index, (start, end) in enumerate(windows):
        dest = out_dir / f"chunk_{index:04d}.wav"
        length = max(0.0, end - start)
        cmd = build_chunk_command(
            input_path, dest, start, length, ffmpeg_bin=ffmpeg_bin
        )
        result = runner(cmd)
        if getattr(result, "returncode", 0) != 0:
            stderr = (getattr(result, "stderr", "") or "")[:500]
            raise FfmpegError(
                f"ffmpeg failed to split chunk {index} "
                f"(rc={getattr(result, 'returncode', None)}): {stderr}"
            )
        chunks.append(
            AudioChunk(
                index=index,
                path=str(dest),
                start_seconds=start,
                end_seconds=end,
            )
        )
    return chunks


def _default_runner(cmd: list[str]):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True)
