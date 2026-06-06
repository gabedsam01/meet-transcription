from __future__ import annotations

import pytest

from app.audio.compress import (
    build_compress_command,
    compress_audio,
    needs_compression,
)
from app.audio.config import AudioConfig
from app.audio.errors import FfmpegError
from app.audio.preprocessor import build_extract_command, extract_audio


class _FakeResult:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


# --- config -----------------------------------------------------------------


def test_config_defaults_disabled():
    cfg = AudioConfig.from_env({})
    assert cfg.enabled is False
    assert cfg.target_sample_rate == 16000
    assert cfg.target_channels == 1
    assert cfg.target_bitrate == 24000
    assert cfg.chunk_max_duration_seconds == 900
    assert cfg.chunk_overlap_seconds == 2
    assert cfg.max_inline_mb == 70
    assert cfg.max_file_api_mb == 99


def test_config_from_env_overrides():
    cfg = AudioConfig.from_env(
        {
            "AUDIO_PREPROCESSING_ENABLED": "true",
            "AUDIO_TARGET_SAMPLE_RATE": "8000",
            "AUDIO_TARGET_CHANNELS": "2",
            "AUDIO_TARGET_BITRATE": "32000",
            "AUDIO_CHUNK_MAX_DURATION_SECONDS": "600",
            "AUDIO_CHUNK_OVERLAP_SECONDS": "5",
            "AUDIO_MAX_INLINE_MB": "20",
            "AUDIO_MAX_FILE_API_MB": "50",
        }
    )
    assert cfg.enabled is True
    assert cfg.target_sample_rate == 8000
    assert cfg.target_channels == 2
    assert cfg.target_bitrate == 32000
    assert cfg.chunk_max_duration_seconds == 600
    assert cfg.chunk_overlap_seconds == 5
    assert cfg.max_inline_mb == 20
    assert cfg.max_file_api_mb == 50


def test_config_from_env_never_raises_on_bad_value():
    cfg = AudioConfig.from_env(
        {"AUDIO_TARGET_SAMPLE_RATE": "abc", "AUDIO_PREPROCESSING_ENABLED": "maybe"}
    )
    assert cfg.target_sample_rate == 16000
    assert cfg.enabled is False  # bad bool falls back to default


def test_config_disabled_classmethod():
    cfg = AudioConfig.disabled()
    assert cfg.enabled is False


# --- extract ----------------------------------------------------------------


def test_build_extract_command_shape():
    cmd = build_extract_command("in.mp4", "out.wav")
    assert cmd[0] == "ffmpeg"
    assert "-vn" in cmd
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert "pcm_s16le" in cmd
    assert cmd[-1] == "out.wav"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "in.mp4"


def test_extract_audio_invokes_runner():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return _FakeResult(returncode=0)

    extract_audio("in.mp4", "out.wav", runner=runner)
    assert len(calls) == 1
    assert calls[0][0] == "ffmpeg"


def test_extract_audio_raises_on_failure():
    def runner(cmd):
        return _FakeResult(returncode=2, stderr="bad input")

    with pytest.raises(FfmpegError):
        extract_audio("in.mp4", "out.wav", runner=runner)


# --- compress ---------------------------------------------------------------


def test_needs_compression():
    assert needs_compression(100 * 1024 * 1024, 99) is True
    assert needs_compression(99 * 1024 * 1024, 99) is False
    assert needs_compression(0, 99) is False


def test_build_compress_command_shape():
    cmd = build_compress_command("in.wav", "out.opus")
    assert cmd[0] == "ffmpeg"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "libopus"
    assert "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == "24000"
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[-1] == "out.opus"


def test_compress_audio_invokes_runner_with_ffmpeg():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return _FakeResult(returncode=0)

    compress_audio("in.wav", "out.opus", runner=runner)
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "libopus" in cmd


def test_compress_audio_raises_on_failure():
    def runner(cmd):
        return _FakeResult(returncode=1, stderr="encode error")

    with pytest.raises(FfmpegError):
        compress_audio("in.wav", "out.opus", runner=runner)
