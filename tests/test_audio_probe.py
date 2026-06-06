from __future__ import annotations

import json

import pytest

from app.audio.errors import AudioProbeError, NoAudioTrackError
from app.audio.probe import AudioInfo, build_ffprobe_command, probe_audio


class _FakeResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ffprobe_json(*, with_audio: bool = True) -> str:
    streams = [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "sample_rate": "0",
            "channels": 0,
        }
    ]
    if with_audio:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
                "bit_rate": "128000",
            }
        )
    payload = {
        "streams": streams,
        "format": {
            "duration": "123.45",
            "bit_rate": "1500000",
            "size": "2048000",
        },
    }
    return json.dumps(payload)


def test_build_ffprobe_command_shape():
    cmd = build_ffprobe_command("in.mp4")
    assert cmd[0] == "ffprobe"
    assert "-print_format" in cmd
    idx = cmd.index("-print_format")
    assert cmd[idx + 1] == "json"
    assert "-show_format" in cmd
    assert "-show_streams" in cmd
    assert cmd[-1] == "in.mp4"


def test_build_ffprobe_command_custom_bin():
    cmd = build_ffprobe_command("a.mp4", ffprobe_bin="/usr/bin/ffprobe")
    assert cmd[0] == "/usr/bin/ffprobe"


def test_probe_audio_parses_mocked_json():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return _FakeResult(returncode=0, stdout=_ffprobe_json(with_audio=True))

    info = probe_audio("meeting.mp4", runner=runner)

    assert calls and calls[0][0] == "ffprobe"
    assert isinstance(info, AudioInfo)
    assert info.has_audio is True
    assert info.duration_seconds == pytest.approx(123.45)
    assert info.sample_rate == 44100
    assert info.channels == 2
    assert info.codec == "aac"
    assert info.bit_rate == 128000
    assert info.size_bytes == 2048000


def test_probe_audio_no_audio_stream_raises():
    def runner(cmd):
        return _FakeResult(returncode=0, stdout=_ffprobe_json(with_audio=False))

    with pytest.raises(NoAudioTrackError):
        probe_audio("silent.mp4", runner=runner)


def test_probe_audio_nonzero_returncode_raises():
    def runner(cmd):
        return _FakeResult(returncode=1, stdout="", stderr="boom")

    with pytest.raises(AudioProbeError):
        probe_audio("broken.mp4", runner=runner)


def test_probe_audio_invalid_json_raises():
    def runner(cmd):
        return _FakeResult(returncode=0, stdout="not json {{{")

    with pytest.raises(AudioProbeError):
        probe_audio("weird.mp4", runner=runner)


def test_probe_audio_missing_format_fields_defaults():
    payload = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "opus",
                    "sample_rate": "16000",
                    "channels": 1,
                }
            ],
            "format": {},
        }
    )

    def runner(cmd):
        return _FakeResult(returncode=0, stdout=payload)

    info = probe_audio("x.mp4", runner=runner)
    assert info.has_audio is True
    assert info.duration_seconds == 0.0
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.codec == "opus"
    assert info.bit_rate is None
    assert info.size_bytes is None
