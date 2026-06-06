from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable
from app.audio.errors import FfmpegError, FfmpegNotFoundError

def is_available() -> bool:
    try:
        import ffmpeg  # noqa: F401
        return shutil.which("ffmpeg") is not None
    except ImportError:
        return False

def check_path_traversal(path: Path, base_dir: Path) -> None:
    try:
        resolved_path = Path(path).resolve()
        resolved_base = Path(base_dir).resolve()
        if not str(resolved_path).startswith(str(resolved_base)):
            raise ValueError(f"Path traversal detected: {path} is not under {base_dir}")
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Invalid path verification: {exc}")

def compress(
    input_path: Path,
    output_path: Path,
    *,
    output_dir: Path,
    sample_rate: int = 16000,
    channels: int = 1,
    format: str = "flac",
    bitrate: str = "24k",
    runner: Callable[[list[str]], object] | None = None,
) -> None:
    if not is_available() and runner is None:
        raise FfmpegNotFoundError("ffmpeg-python wrapper or ffmpeg not available")

    import ffmpeg
    check_path_traversal(output_path, output_dir)

    try:
        stream = ffmpeg.input(str(input_path))
        audio = stream.audio
        
        kwargs = {
            "ar": sample_rate,
            "ac": channels,
        }
        if format == "flac":
            kwargs["c:a"] = "flac"
        elif format in ("mp3", "ogg"):
            kwargs["b:a"] = bitrate
            if format == "ogg":
                kwargs["c:a"] = "libopus"

        out = ffmpeg.output(audio, str(output_path), vn=None, **kwargs)
        out.run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except Exception as exc:
        raise FfmpegError(f"ffmpeg-python compression failed: {exc}")

def chunk(
    input_path: Path,
    output_dir: Path,
    *,
    segment_time_seconds: int = 900,
    format: str = "mp3",
    bitrate: str = "24k",
    runner: Callable[[list[str]], object] | None = None,
) -> list[Path]:
    if not is_available() and runner is None:
        raise FfmpegNotFoundError("ffmpeg-python wrapper or ffmpeg not available")

    import ffmpeg
    output_pattern = output_dir / f"chunk_%03d.{format}"
    check_path_traversal(output_pattern, output_dir)

    try:
        stream = ffmpeg.input(str(input_path))
        audio = stream.audio

        kwargs = {
            "ar": 16000,
            "ac": 1,
            "f": "segment",
            "segment_time": segment_time_seconds,
            "reset_timestamps": 1,
        }
        if format == "flac":
            kwargs["c:a"] = "flac"
        elif format in ("mp3", "ogg"):
            kwargs["b:a"] = bitrate
            if format == "ogg":
                kwargs["c:a"] = "libopus"

        out = ffmpeg.output(audio, str(output_pattern), vn=None, **kwargs)
        out.run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except Exception as exc:
        raise FfmpegError(f"ffmpeg-python chunking failed: {exc}")

    files = sorted(list(output_dir.glob(f"chunk_*.{format}")))
    for f in files:
        check_path_traversal(f, output_dir)
    return files
