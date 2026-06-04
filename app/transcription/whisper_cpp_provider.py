from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Callable

from app.transcription.audio import extract_audio_to_wav
from app.transcription.config import TranscriptionConfig
from app.transcription.normalizer import (
    normalized_payload,
    render_local_text,
    segment,
    segments_text,
)
from app.transcription.provider import TranscriptionResult

LOGGER = logging.getLogger(__name__)


class WhisperCppProvider:
    """CPU-only whisper.cpp provider driven via the ``whisper-cli`` subprocess.

    The MP4 is first decoded to 16 kHz mono WAV (ffmpeg), then transcribed with
    JSON output (``-oj``); the JSON offsets become normalized segments. If JSON is
    unavailable the stdout/txt is parsed into a single segment. The subprocess and
    audio extractor are injectable so tests need no real binary, model, or ffmpeg.
    """

    def __init__(
        self,
        config: TranscriptionConfig,
        *,
        runner: Callable[[list[str]], object] | None = None,
        audio_extractor: Callable[[str, str], None] | None = None,
    ) -> None:
        self._config = config
        self._runner = runner or _default_runner
        self._audio_extractor = audio_extractor or extract_audio_to_wav

    def transcribe(
        self, source_path: str | Path, *, original_name: str, file_id: str
    ) -> TranscriptionResult:
        # whisper.cpp has no auto-download; never build a broken "-m <empty>" cmd.
        if not self._config.model_path:
            raise RuntimeError(
                "whisper.cpp requires LOCAL_TRANSCRIPTION_MODEL_PATH (no auto-download)."
            )
        workdir = Path(source_path).parent / "whispercpp"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            wav_path = workdir / "audio.wav"
            self._audio_extractor(str(source_path), str(wav_path))

            out_prefix = str(workdir / "out")
            cmd = self._build_command(str(wav_path), out_prefix)
            result = self._runner(cmd)
            if getattr(result, "returncode", 0) != 0:
                stderr = (getattr(result, "stderr", "") or "")[:500]
                raise RuntimeError(
                    f"whisper.cpp failed (rc={result.returncode}): {stderr}"
                )

            segments, language = self._parse_output(out_prefix, result)
            payload = normalized_payload(
                provider="local",
                engine="whisper-cpp",
                model=self._config.model,
                language=language or self._config.language,
                text=segments_text(segments),
                segments=segments,
            )
            text = render_local_text(payload, original_name, file_id)
            return TranscriptionResult(text=text, payload=payload)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _build_command(self, wav_path: str, out_prefix: str) -> list[str]:
        language = self._config.language if self._config.language != "auto" else "auto"
        return [
            self._config.whisper_cpp_binary or "whisper-cli",
            "-m",
            self._config.model_path or "",
            "-f",
            wav_path,
            "-t",
            str(self._config.threads),
            "-l",
            language,
            "-oj",
            "-of",
            out_prefix,
        ]

    def _parse_output(self, out_prefix: str, result) -> tuple[list[dict], str | None]:
        json_path = Path(out_prefix + ".json")
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            segments = [
                segment(
                    (item.get("offsets", {}).get("from") or 0) / 1000.0,
                    (item.get("offsets", {}).get("to") or 0) / 1000.0,
                    item.get("text") or "",
                )
                for item in data.get("transcription", [])
                if (item.get("text") or "").strip()
            ]
            language = (data.get("result") or {}).get("language")
            if segments or language:
                return segments, language

        # Fallback: a plain .txt sidecar, else whatever the process printed.
        txt_path = Path(out_prefix + ".txt")
        if txt_path.exists():
            text = txt_path.read_text(encoding="utf-8").strip()
        else:
            text = (getattr(result, "stdout", "") or "").strip()
        return ([segment(0.0, 0.0, text)] if text else []), None


def _default_runner(cmd: list[str]):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True)
