import pytest

from app.transcription.audio import build_ffmpeg_command, extract_audio_to_wav


def test_build_ffmpeg_command_targets_16k_mono_wav():
    cmd = build_ffmpeg_command("in.mp4", "out.wav")
    assert cmd[0] == "ffmpeg"
    assert "in.mp4" in cmd
    assert "out.wav" in cmd
    assert "16000" in cmd  # -ar 16000
    # -ac 1 (mono): the token after -ac is "1"
    assert cmd[cmd.index("-ac") + 1] == "1"


def test_extract_audio_runs_command(tmp_path):
    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    def runner(cmd):
        captured["cmd"] = cmd
        return _Result()

    extract_audio_to_wav("in.mp4", tmp_path / "out.wav", runner=runner)
    assert captured["cmd"][0] == "ffmpeg"


def test_extract_audio_raises_on_failure(tmp_path):
    class _Result:
        returncode = 1
        stderr = "ffmpeg boom"

    with pytest.raises(RuntimeError, match="ffmpeg"):
        extract_audio_to_wav("in.mp4", tmp_path / "out.wav", runner=lambda cmd: _Result())
