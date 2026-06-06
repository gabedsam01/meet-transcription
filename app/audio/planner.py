from __future__ import annotations

from pathlib import Path
from app.audio.types import AudioCompressionPlan
from app.audio.backends import (
    ffmpeg_cli,
    ffmpeg_python_backend,
    pydub_backend,
    moviepy_backend,
)
from app.audio.errors import FfmpegNotFoundError

def plan_compression(
    input_path: Path,
    output_dir: Path,
    target_mb: int,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    preferred_format: str = "flac",
    fallback_format: str = "mp3",
    bitrate: str = "24k",
    allow_chunking: bool = True,
) -> AudioCompressionPlan:
    return AudioCompressionPlan(
        input_path=input_path,
        output_dir=output_dir,
        target_mb=target_mb,
        sample_rate=sample_rate,
        channels=channels,
        preferred_format=preferred_format,
        fallback_format=fallback_format,
        bitrate=bitrate,
        allow_chunking=allow_chunking,
    )

def select_backend(plan: AudioCompressionPlan) -> str:
    if not plan.input_path.exists():
        return "no-op"
        
    size_bytes = plan.input_path.stat().st_size
    target_bytes = plan.target_mb * 1024 * 1024
    if size_bytes <= target_bytes:
        return "no-op"

    if ffmpeg_cli.is_available():
        return "ffmpeg_cli"
    if ffmpeg_python_backend.is_available():
        return "ffmpeg_python"
    if pydub_backend.is_available():
        return "pydub"
    if moviepy_backend.is_available():
        return "moviepy"

    raise FfmpegNotFoundError("Nenhum backend do ffmpeg disponível no sistema.")
