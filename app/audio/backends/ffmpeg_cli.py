from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable
from app.audio.errors import FfmpegError, FfmpegNotFoundError

def is_available() -> bool:
    return shutil.which("ffmpeg") is not None

def _default_runner(cmd: list[str]):
    return subprocess.run(cmd, capture_output=True, text=True)

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
        raise FfmpegNotFoundError("ffmpeg executable not found in system path")

    check_path_traversal(output_path, output_dir)
    run = runner or _default_runner

    # 1. Try with -map 0:a:0
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-map",
        "0:a:0",
    ]
    if format == "flac":
        cmd.extend(["-c:a", "flac"])
    elif format in ("mp3", "ogg"):
        cmd.extend(["-b:a", bitrate])
        if format == "ogg":
            cmd.extend(["-c:a", "libopus"])
    
    cmd.append(str(output_path))

    res = run(cmd)
    if getattr(res, "returncode", 0) != 0:
        # Fallback: try without -map 0:a:0
        cmd_fallback = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
        ]
        if format == "flac":
            cmd_fallback.extend(["-c:a", "flac"])
        elif format in ("mp3", "ogg"):
            cmd_fallback.extend(["-b:a", bitrate])
            if format == "ogg":
                cmd_fallback.extend(["-c:a", "libopus"])
        cmd_fallback.append(str(output_path))

        res_fallback = run(cmd_fallback)
        if getattr(res_fallback, "returncode", 0) != 0:
            stderr = getattr(res_fallback, "stderr", "") or getattr(res, "stderr", "") or ""
            raise FfmpegError(f"ffmpeg compression failed: {stderr[:500]}")

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
        raise FfmpegNotFoundError("ffmpeg executable not found in system path")

    output_pattern = output_dir / f"chunk_%03d.{format}"
    check_path_traversal(output_pattern, output_dir)
    run = runner or _default_runner

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "segment",
        "-segment_time",
        str(segment_time_seconds),
        "-reset_timestamps",
        "1",
    ]
    if format == "flac":
        cmd.extend(["-c:a", "flac"])
    elif format in ("mp3", "ogg"):
        cmd.extend(["-b:a", bitrate])
        if format == "ogg":
            cmd.extend(["-c:a", "libopus"])
            
    cmd.append(str(output_pattern))

    res = run(cmd)
    if getattr(res, "returncode", 0) != 0:
        raise FfmpegError(f"ffmpeg chunking failed: {getattr(res, 'stderr', '')[:500]}")

    # Discover created files matching pattern chunk_*.{format}
    files = sorted(list(output_dir.glob(f"chunk_*.{format}")))
    for f in files:
        check_path_traversal(f, output_dir)
    return files
