import json
from pathlib import Path

import pytest

from app.transcription.config import TranscriptionConfig
from app.transcription.whisper_cpp_provider import WhisperCppProvider


def _cfg(**over):
    env = {
        "LOCAL_TRANSCRIPTION_ENABLED": "true",
        "LOCAL_TRANSCRIPTION_ENGINE": "whisper-cpp",
        "LOCAL_TRANSCRIPTION_MODEL": "small",
        "LOCAL_TRANSCRIPTION_QUANTIZATION": "q4_0",
        "LOCAL_TRANSCRIPTION_MODEL_PATH": "/models/ggml-small-q4_0.bin",
        "WHISPER_CPP_BINARY": "/usr/local/bin/whisper-cli",
        "LOCAL_TRANSCRIPTION_THREADS": "4",
    }
    env.update(over)
    return TranscriptionConfig.from_env(env)


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _json_runner(transcription, language="pt"):
    """A fake whisper-cli that writes the JSON sidecar at the -of prefix."""
    captured = {}

    def run(cmd):
        captured["cmd"] = cmd
        prefix = cmd[cmd.index("-of") + 1]
        Path(prefix + ".json").write_text(
            json.dumps({"result": {"language": language}, "transcription": transcription}),
            encoding="utf-8",
        )
        return _Completed(0)

    run.captured = captured
    return run


def _wav_extractor(src, dst):
    Path(dst).write_bytes(b"fake wav")


def test_parses_json_offsets_into_segments(tmp_path):
    transcription = [
        {"offsets": {"from": 0, "to": 2000}, "text": " Olá"},
        {"offsets": {"from": 2000, "to": 4000}, "text": " mundo"},
    ]
    runner = _json_runner(transcription, language="pt")
    provider = WhisperCppProvider(
        _cfg(LOCAL_TRANSCRIPTION_LANGUAGE="auto"),
        runner=runner,
        audio_extractor=_wav_extractor,
    )

    result = provider.transcribe(tmp_path / "x.mp4", original_name="x.mp4", file_id="id1")

    assert result.payload["provider"] == "local"
    assert result.payload["engine"] == "whisper-cpp"
    assert result.payload["language"] == "pt"
    assert [s["text"] for s in result.payload["segments"]] == ["Olá", "mundo"]
    assert result.payload["segments"][0]["start"] == 0.0
    assert result.payload["segments"][0]["end"] == 2.0
    cmd = runner.captured["cmd"]
    assert cmd[0] == "/usr/local/bin/whisper-cli"
    assert "/models/ggml-small-q4_0.bin" in cmd
    assert cmd[cmd.index("-t") + 1] == "4"
    assert cmd[cmd.index("-l") + 1] == "auto"  # auto language forwarded
    assert "-oj" in cmd


def test_explicit_language_is_forwarded(tmp_path):
    runner = _json_runner([], language="en")
    provider = WhisperCppProvider(
        _cfg(LOCAL_TRANSCRIPTION_LANGUAGE="en"), runner=runner, audio_extractor=_wav_extractor
    )
    provider.transcribe(tmp_path / "x.mp4", original_name="x", file_id="i")
    cmd = runner.captured["cmd"]
    assert cmd[cmd.index("-l") + 1] == "en"


def test_txt_fallback_when_no_json(tmp_path):
    def run(cmd):
        return _Completed(0, stdout="plain whisper text")

    provider = WhisperCppProvider(_cfg(), runner=run, audio_extractor=_wav_extractor)
    result = provider.transcribe(tmp_path / "x.mp4", original_name="x", file_id="i")
    assert len(result.payload["segments"]) == 1
    assert result.payload["text"] == "plain whisper text"


def test_nonzero_exit_raises(tmp_path):
    provider = WhisperCppProvider(
        _cfg(), runner=lambda cmd: _Completed(1, stderr="boom"), audio_extractor=_wav_extractor
    )
    with pytest.raises(RuntimeError, match="whisper.cpp"):
        provider.transcribe(tmp_path / "x.mp4", original_name="x", file_id="i")


def test_audio_extractor_is_invoked_with_wav_destination(tmp_path):
    calls = []

    def extractor(src, dst):
        calls.append((str(src), str(dst)))
        Path(dst).write_bytes(b"wav")

    runner = _json_runner([{"offsets": {"from": 0, "to": 1000}, "text": "hi"}])
    provider = WhisperCppProvider(_cfg(), runner=runner, audio_extractor=extractor)
    provider.transcribe(tmp_path / "video.mp4", original_name="video.mp4", file_id="i")

    assert len(calls) == 1
    assert calls[0][0].endswith("video.mp4")
    assert calls[0][1].endswith(".wav")


def test_missing_model_path_raises_clear_error(tmp_path):
    # Defense in depth: even if reached with no model_path, never build a broken
    # "whisper-cli -m  -f ..." command — fail loudly instead.
    provider = WhisperCppProvider(
        _cfg(LOCAL_TRANSCRIPTION_MODEL_PATH=""),
        runner=lambda cmd: _Completed(0),
        audio_extractor=_wav_extractor,
    )
    with pytest.raises(RuntimeError, match="MODEL_PATH"):
        provider.transcribe(tmp_path / "x.mp4", original_name="x", file_id="i")


def test_work_dir_is_cleaned_up(tmp_path):
    runner = _json_runner([{"offsets": {"from": 0, "to": 1000}, "text": "hi"}])
    provider = WhisperCppProvider(_cfg(), runner=runner, audio_extractor=_wav_extractor)
    provider.transcribe(tmp_path / "video.mp4", original_name="video.mp4", file_id="i")
    # The provider's scratch dir must not survive the call (tmp isolation per job).
    assert not (tmp_path / "whispercpp").exists()
