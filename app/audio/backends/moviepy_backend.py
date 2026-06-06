from __future__ import annotations

from pathlib import Path
from typing import Callable
from app.audio.errors import FfmpegError, FfmpegNotFoundError

def is_available() -> bool:
    try:
        from moviepy.editor import AudioFileClip  # noqa: F401
        return True
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
    if not is_available():
        raise FfmpegNotFoundError("moviepy backend not available")

    from moviepy.editor import AudioFileClip
    check_path_traversal(output_path, output_dir)

    codec_map = {
        "flac": "flac",
        "mp3": "libmp3lame",
        "ogg": "libvorbis",
    }
    codec = codec_map.get(format)

    try:
        clip = AudioFileClip(str(input_path))
        clip.write_audiofile(
            str(output_path),
            fps=sample_rate,
            nbytes=2,
            codec=codec,
            bitrate=bitrate if format in ("mp3", "ogg") else None,
            ffmpeg_params=["-ac", str(channels)],
            logger=None,
        )
        clip.close()
    except Exception as exc:
        raise FfmpegError(f"moviepy compression failed: {exc}")

def chunk(
    input_path: Path,
    output_dir: Path,
    *,
    segment_time_seconds: int = 900,
    format: str = "mp3",
    bitrate: str = "24k",
    runner: Callable[[list[str]], object] | None = None,
) -> list[Path]:
    if not is_available():
        raise FfmpegNotFoundError("moviepy backend not available")

    from moviepy.editor import AudioFileClip
    codec_map = {
        "flac": "flac",
        "mp3": "libmp3lame",
        "ogg": "libvorbis",
    }
    codec = codec_map.get(format)

    try:
        clip = AudioFileClip(str(input_path))
        duration = clip.duration
        
        chunks = []
        index = 0
        start = 0.0
        while start < duration:
            end = min(start + segment_time_seconds, duration)
            subclip = clip.subclip(start, end)
            chunk_path = output_dir / f"chunk_{index:03d}.{format}"
            check_path_traversal(chunk_path, output_dir)
            
            subclip.write_audiofile(
                str(chunk_path),
                fps=16000,
                nbytes=2,
                codec=codec,
                bitrate=bitrate if format in ("mp3", "ogg") else None,
                ffmpeg_params=["-ac", "1"],
                logger=None,
            )
            chunks.append(chunk_path)
            start = end
            index += 1
            
        clip.close()
        return chunks
    except Exception as exc:
        raise FfmpegError(f"moviepy chunking failed: {exc}")
