from __future__ import annotations

from pathlib import Path
from typing import Callable

# whisper.cpp expects 16 kHz mono PCM WAV input. faster-whisper decodes media
# itself (PyAV) and does NOT need this. ffmpeg is an optional runtime dependency,
# documented as a build arg / image requirement for the whisper.cpp engine.


def build_ffmpeg_command(
    source: str | Path, dest: str | Path, *, ffmpeg_bin: str = "ffmpeg"
) -> list[str]:
    return [
        ffmpeg_bin,
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]


def extract_audio_to_wav(
    source: str | Path,
    dest: str | Path,
    *,
    runner: Callable[[list[str]], object] | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> None:
    runner = runner or _default_runner
    cmd = build_ffmpeg_command(source, dest, ffmpeg_bin=ffmpeg_bin)
    result = runner(cmd)
    if getattr(result, "returncode", 0) != 0:
        stderr = (getattr(result, "stderr", "") or "")[:500]
        raise RuntimeError(f"ffmpeg failed to extract audio (rc={result.returncode}): {stderr}")


def _default_runner(cmd: list[str]):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True)
