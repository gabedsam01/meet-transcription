"""Audio preprocessing utilities (probe, extract, compress, chunk, stitch).

A tested, side-effect-free-by-default library: every operation that runs ffmpeg /
ffprobe goes through an injectable ``runner`` so tests need no real binary. The
worker wires only the pieces it needs; the rest are reusable building blocks.
"""

from __future__ import annotations

from app.audio.chunking import (
    AudioChunk,
    build_chunk_command,
    chunk_audio,
    plan_chunks,
)
from app.audio.compress import (
    build_compress_command,
    compress_audio,
    needs_compression,
)
from app.audio.config import AudioConfig
from app.audio.errors import (
    AudioError,
    AudioProbeError,
    FfmpegError,
    NoAudioTrackError,
)
from app.audio.preprocessor import build_extract_command, extract_audio
from app.audio.probe import AudioInfo, build_ffprobe_command, probe_audio
from app.audio.stitch import stitch_transcript_chunks

__all__ = [
    # config
    "AudioConfig",
    # errors
    "AudioError",
    "NoAudioTrackError",
    "AudioProbeError",
    "FfmpegError",
    # probe
    "AudioInfo",
    "build_ffprobe_command",
    "probe_audio",
    # preprocessor
    "build_extract_command",
    "extract_audio",
    # compress
    "needs_compression",
    "build_compress_command",
    "compress_audio",
    # chunking
    "AudioChunk",
    "plan_chunks",
    "build_chunk_command",
    "chunk_audio",
    # stitch
    "stitch_transcript_chunks",
]
