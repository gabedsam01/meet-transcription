from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.audio.errors import FfmpegError

# Extract a clean 16 kHz mono PCM WAV from the source media. PCM (pcm_s16le) is
# the lossless intermediate the local engines decode directly; compression to a
# lossy codec (for upload size limits) is a separate step in compress.py.


def build_extract_command(
    source: str | Path,
    dest: str | Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_bin,
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]


def extract_audio(
    input_path: str | Path,
    output_path: str | Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    runner: Callable[[list[str]], object] | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> None:
    """Extract audio from ``input_path`` to a WAV at ``output_path``.

    ``runner`` is injected in tests; it returns an object with ``.returncode``.
    On a nonzero return code we raise :class:`FfmpegError` (ffmpeg stderr stays
    in the technical message, never in ``user_message``).
    """

    runner = runner or _default_runner
    cmd = build_extract_command(
        input_path,
        output_path,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg_bin=ffmpeg_bin,
    )
    result = runner(cmd)
    if getattr(result, "returncode", 0) != 0:
        stderr = (getattr(result, "stderr", "") or "")[:500]
        raise FfmpegError(
            f"ffmpeg failed to extract audio "
            f"(rc={getattr(result, 'returncode', None)}): {stderr}"
        )


def _default_runner(cmd: list[str]):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True)
