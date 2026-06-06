from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class AudioCompressionPlan:
    input_path: Path
    output_dir: Path
    target_mb: int
    sample_rate: int = 16000
    channels: int = 1
    preferred_format: str = "flac"
    fallback_format: str = "mp3"
    bitrate: str = "24k"
    allow_chunking: bool = True

@dataclass
class PreparedAudio:
    files: list[Path]
    total_duration_seconds: float | None
    was_compressed: bool
    was_chunked: bool
    format: str
    warnings: list[str] = field(default_factory=list)
