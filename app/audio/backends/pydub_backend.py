from __future__ import annotations

from pathlib import Path
from typing import Callable
from app.audio.errors import FfmpegError, FfmpegNotFoundError

def is_available() -> bool:
    try:
        from pydub import AudioSegment  # noqa: F401
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
        raise FfmpegNotFoundError("pydub backend not available")

    from pydub import AudioSegment
    check_path_traversal(output_path, output_dir)

    try:
        sound = AudioSegment.from_file(str(input_path))
        sound = sound.set_frame_rate(sample_rate).set_channels(channels)
        
        # Pydub export format/bitrate logic
        export_kwargs = {}
        if format in ("mp3", "ogg"):
            export_kwargs["bitrate"] = bitrate
            
        sound.export(str(output_path), format=format, **export_kwargs)
    except Exception as exc:
        raise FfmpegError(f"pydub compression failed: {exc}")

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
        raise FfmpegNotFoundError("pydub backend not available")

    from pydub import AudioSegment
    try:
        sound = AudioSegment.from_file(str(input_path))
        segment_ms = segment_time_seconds * 1000
        
        chunks = []
        for index, start_ms in enumerate(range(0, len(sound), segment_ms)):
            chunk_segment = sound[start_ms : start_ms + segment_ms]
            chunk_path = output_dir / f"chunk_{index:03d}.{format}"
            check_path_traversal(chunk_path, output_dir)
            
            chunk_segment = chunk_segment.set_frame_rate(16000).set_channels(1)
            
            export_kwargs = {}
            if format in ("mp3", "ogg"):
                export_kwargs["bitrate"] = bitrate
                
            chunk_segment.export(str(chunk_path), format=format, **export_kwargs)
            chunks.append(chunk_path)
            
        return chunks
    except Exception as exc:
        raise FfmpegError(f"pydub chunking failed: {exc}")
