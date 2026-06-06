from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.audio.errors import FfmpegError

# Re-encode audio to a lossy codec (Opus by default) to fit upload size limits
# (e.g. an inline/file-API ceiling). This trades fidelity for size and is only
# needed when needs_compression() says the file is over the target.


def needs_compression(size_bytes: int, target_max_mb: int) -> bool:
    return size_bytes > target_max_mb * 1024 * 1024


def build_compress_command(
    source: str | Path,
    dest: str | Path,
    *,
    codec: str = "libopus",
    bitrate: int = 24000,
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
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        str(codec),
        "-b:a",
        str(bitrate),
        str(dest),
    ]


def compress_audio(
    input_path: str | Path,
    output_path: str | Path,
    *,
    target_max_mb: int = 99,
    codec: str = "libopus",
    bitrate: int = 24000,
    sample_rate: int = 16000,
    channels: int = 1,
    runner: Callable[[list[str]], object] | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> None:
    """Re-encode ``input_path`` to a smaller ``output_path``.

    ``target_max_mb`` is accepted for symmetry with :func:`needs_compression`
    (callers decide whether to compress); this function always performs the
    re-encode when invoked. ``runner`` is injected in tests; on a nonzero return
    code we raise :class:`FfmpegError`.
    """

    runner = runner or _default_runner
    cmd = build_compress_command(
        input_path,
        output_path,
        codec=codec,
        bitrate=bitrate,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg_bin=ffmpeg_bin,
    )
    result = runner(cmd)
    if getattr(result, "returncode", 0) != 0:
        stderr = (getattr(result, "stderr", "") or "")[:500]
        raise FfmpegError(
            f"ffmpeg failed to compress audio "
            f"(rc={getattr(result, 'returncode', None)}): {stderr}"
        )


def _default_runner(cmd: list[str]):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True)
