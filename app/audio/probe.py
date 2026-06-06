from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.audio.errors import AudioProbeError, NoAudioTrackError


@dataclass(frozen=True)
class AudioInfo:
    """What ffprobe tells us about a media file's first audio stream."""

    has_audio: bool
    duration_seconds: float
    sample_rate: int
    channels: int
    codec: str
    bit_rate: int | None
    size_bytes: int | None


def build_ffprobe_command(
    source: str | Path, *, ffprobe_bin: str = "ffprobe"
) -> list[str]:
    return [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]


def probe_audio(
    source: str | Path,
    *,
    runner: Callable[[list[str]], object] | None = None,
    ffprobe_bin: str = "ffprobe",
) -> AudioInfo:
    """Probe ``source`` with ffprobe and return its first audio stream's info.

    ``runner`` is injected in tests; it takes the command list and returns an
    object with ``.returncode`` and ``.stdout`` (JSON text). On a nonzero return
    code or unparseable JSON we raise :class:`AudioProbeError`; when there is no
    audio stream we raise :class:`NoAudioTrackError`. ffprobe stderr stays in the
    technical message, never in ``user_message``.
    """

    runner = runner or _default_runner
    cmd = build_ffprobe_command(source, ffprobe_bin=ffprobe_bin)
    result = runner(cmd)

    returncode = getattr(result, "returncode", 0)
    if returncode != 0:
        stderr = (getattr(result, "stderr", "") or "")[:500]
        raise AudioProbeError(
            f"ffprobe failed (rc={returncode}) for {source!r}: {stderr}"
        )

    stdout = getattr(result, "stdout", "") or ""
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError) as exc:
        raise AudioProbeError(
            f"ffprobe returned unparseable output for {source!r}: {exc}"
        ) from exc

    streams = data.get("streams") or []
    audio_stream = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )
    if audio_stream is None:
        raise NoAudioTrackError(f"no audio stream found in {source!r}")

    fmt = data.get("format") or {}
    return AudioInfo(
        has_audio=True,
        duration_seconds=_float(fmt.get("duration"), 0.0),
        sample_rate=_int(audio_stream.get("sample_rate"), 0),
        channels=_int(audio_stream.get("channels"), 0),
        codec=str(audio_stream.get("codec_name") or "").strip(),
        bit_rate=_optional_int(audio_stream.get("bit_rate") or fmt.get("bit_rate")),
        size_bytes=_optional_int(fmt.get("size")),
    )


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _default_runner(cmd: list[str]):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True)
