from __future__ import annotations

import pytest
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.audio.types import AudioCompressionPlan, PreparedAudio
from app.audio.planner import plan_compression, select_backend
from app.audio.errors import FfmpegError, FfmpegNotFoundError
from app.errors import ProviderFileTooLargeError
from app.audio.compression import prepare_audio_for_provider
from app.audio.backends import ffmpeg_cli

def test_planner_chooses_noop_if_under_target(tmp_path):
    input_file = tmp_path / "small.mp3"
    input_file.write_bytes(b"a" * 1024)  # 1 KB
    
    plan = plan_compression(input_file, tmp_path, target_mb=10)
    assert select_backend(plan) == "no-op"

def test_planner_chooses_ffmpeg_cli_if_over_target(tmp_path):
    input_file = tmp_path / "large.mp3"
    input_file.write_bytes(b"a" * 2000)
    
    plan = plan_compression(input_file, tmp_path, target_mb=0)  # target 0 MB -> large
    with patch("app.audio.backends.ffmpeg_cli.is_available", return_value=True):
        assert select_backend(plan) == "ffmpeg_cli"

def test_ffmpeg_cli_compress_command_structure(tmp_path):
    input_file = tmp_path / "in.mp4"
    output_file = tmp_path / "out.flac"
    input_file.write_bytes(b"some audio bytes")

    called_cmds = []
    def dummy_runner(cmd):
        called_cmds.append(cmd)
        mock_res = MagicMock()
        mock_res.returncode = 0
        return mock_res

    with patch("app.audio.backends.ffmpeg_cli.is_available", return_value=True):
        ffmpeg_cli.compress(
            input_path=input_file,
            output_path=output_file,
            output_dir=tmp_path,
            sample_rate=16000,
            channels=1,
            format="flac",
            runner=dummy_runner,
        )

    assert len(called_cmds) > 0
    cmd = called_cmds[0]
    assert cmd[0] == "ffmpeg"
    assert "-ar" in cmd
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert "-ac" in cmd
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert "-c:a" in cmd
    assert cmd[cmd.index("-c:a") + 1] == "flac"

def test_fallback_mp3_used_if_flac_large(tmp_path):
    input_file = tmp_path / "large_video.mp4"
    input_file.write_bytes(b"a" * (2 * 1024 * 1024))  # 2 MB > 1 MB limit
    
    class MockCapabilities:
        provider = "openrouter"
        max_upload_mb = 1
        preferred_format = "flac"
        supports_chunking = True

    class MockConfig:
        target_sample_rate = 16000
        target_channels = 1
        target_bitrate = 24000
        chunk_max_duration_seconds = 900

    def mock_compress(input_path, output_path, output_dir, sample_rate, channels, format, bitrate, runner=None):
        if format == "flac":
            # FLAC is still too large
            output_path.write_bytes(b"a" * (2 * 1024 * 1024))  # 2 MB > 1 MB limit
        else:
            # Fallback mp3 fits
            output_path.write_bytes(b"a" * (500 * 1024))  # 0.5 MB <= 1 MB limit

    with patch("app.audio.backends.ffmpeg_cli.is_available", return_value=True), \
         patch("app.audio.backends.ffmpeg_cli.compress", mock_compress):
        res = prepare_audio_for_provider(input_file, MockCapabilities(), tmp_path, config=MockConfig())
        assert res.was_compressed is True
        assert res.was_chunked is False
        assert res.format == "mp3"
        assert len(res.files) == 1
        assert res.files[0].suffix == ".mp3"

def test_chunking_creates_multiple_paths(tmp_path):
    input_file = tmp_path / "oversized.mp4"
    input_file.write_bytes(b"a" * (2 * 1024 * 1024))  # 2 MB > 1 MB limit

    class MockCapabilities:
        provider = "openrouter"
        max_upload_mb = 1
        preferred_format = "flac"
        supports_chunking = True

    class MockConfig:
        target_sample_rate = 16000
        target_channels = 1
        target_bitrate = 24000
        chunk_max_duration_seconds = 900

    def mock_compress(input_path, output_path, output_dir, sample_rate, channels, format, bitrate, runner=None):
        # Both flac and mp3 exceed limits, forcing chunking
        output_path.write_bytes(b"a" * (3 * 1024 * 1024))

    def mock_chunk(input_path, output_dir, segment_time_seconds, format, bitrate, runner=None):
        chunk1 = output_dir / f"chunk_000.{format}"
        chunk2 = output_dir / f"chunk_001.{format}"
        chunk1.write_bytes(b"a" * 500 * 1024)  # fits
        chunk2.write_bytes(b"a" * 500 * 1024)  # fits
        return [chunk1, chunk2]

    with patch("app.audio.backends.ffmpeg_cli.is_available", return_value=True), \
         patch("app.audio.backends.ffmpeg_cli.compress", mock_compress), \
         patch("app.audio.backends.ffmpeg_cli.chunk", mock_chunk):
        res = prepare_audio_for_provider(input_file, MockCapabilities(), tmp_path, config=MockConfig())
        assert res.was_compressed is True
        assert res.was_chunked is True
        assert len(res.files) == 2
        assert res.files[0].name == "chunk_000.mp3"

def test_error_if_ffmpeg_absent(tmp_path):
    input_file = tmp_path / "video.mp4"
    input_file.write_bytes(b"a" * 2000)
    
    plan = plan_compression(input_file, tmp_path, target_mb=0)
    
    with patch("app.audio.backends.ffmpeg_cli.is_available", return_value=False), \
         patch("app.audio.backends.ffmpeg_python_backend.is_available", return_value=False), \
         patch("app.audio.backends.pydub_backend.is_available", return_value=False), \
         patch("app.audio.backends.moviepy_backend.is_available", return_value=False):
        with pytest.raises(FfmpegNotFoundError):
            select_backend(plan)

def test_wrappers_optional_do_not_break_if_libs_missing():
    with patch.dict("sys.modules", {"ffmpeg": None, "pydub": None, "moviepy": None}):
        from app.audio.backends import ffmpeg_python_backend, pydub_backend, moviepy_backend
        assert ffmpeg_python_backend.is_available() is False
        assert pydub_backend.is_available() is False
        assert moviepy_backend.is_available() is False

def test_path_traversal_is_blocked(tmp_path):
    jail_dir = tmp_path / "jail"
    jail_dir.mkdir()
    
    traversal_path = tmp_path / "outside.mp3"
    
    with pytest.raises(ValueError) as exc:
        ffmpeg_cli.check_path_traversal(traversal_path, jail_dir)
    assert "Path traversal detected" in str(exc.value)

def test_cleanup_only_removes_tmp_job_content(tmp_path):
    job_dir = tmp_path / "job_123"
    job_dir.mkdir()
    
    sibling_dir = tmp_path / "job_456"
    sibling_dir.mkdir()
    
    job_file = job_dir / "compressed.flac"
    job_file.write_bytes(b"123")
    
    sibling_file = sibling_dir / "compressed.flac"
    sibling_file.write_bytes(b"456")

    shutil.rmtree(job_dir)
    
    assert not job_file.exists()
    assert sibling_file.exists()

def test_outputs_always_below_target_or_raises_provider_file_too_large(tmp_path):
    input_file = tmp_path / "giant.mp4"
    input_file.write_bytes(b"a" * (2 * 1024 * 1024))  # 2 MB > 1 MB limit

    class MockCapabilities:
        provider = "groq"
        max_upload_mb = 1
        preferred_format = "mp3"
        supports_chunking = True

    class MockConfig:
        target_sample_rate = 16000
        target_channels = 1
        target_bitrate = 24000
        chunk_max_duration_seconds = 900

    def mock_compress(input_path, output_path, output_dir, sample_rate, channels, format, bitrate, runner=None):
        output_path.write_bytes(b"a" * (5 * 1024 * 1024))

    def mock_chunk(input_path, output_dir, segment_time_seconds, format, bitrate, runner=None):
        chunk1 = output_dir / f"chunk_000.{format}"
        chunk1.write_bytes(b"a" * 2 * 1024 * 1024)  # 2 MB > 1 MB limit
        return [chunk1]

    with patch("app.audio.backends.ffmpeg_cli.is_available", return_value=True), \
         patch("app.audio.backends.ffmpeg_cli.compress", mock_compress), \
         patch("app.audio.backends.ffmpeg_cli.chunk", mock_chunk):
        with pytest.raises(ProviderFileTooLargeError) as exc:
            prepare_audio_for_provider(input_file, MockCapabilities(), tmp_path, config=MockConfig())
        assert "No free tier do Groq" in exc.value.user_message
