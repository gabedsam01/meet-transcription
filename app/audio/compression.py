from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable
from app.audio.types import AudioCompressionPlan, PreparedAudio
from app.audio.planner import plan_compression, select_backend
from app.audio.backends import (
    ffmpeg_cli,
    ffmpeg_python_backend,
    pydub_backend,
    moviepy_backend,
)
from app.audio.errors import AudioError
from app.errors import ProviderFileTooLargeError

LOGGER = logging.getLogger(__name__)

def _get_backend(name: str):
    if name == "ffmpeg_cli":
        return ffmpeg_cli
    if name == "ffmpeg_python":
        return ffmpeg_python_backend
    if name == "pydub":
        return pydub_backend
    if name == "moviepy":
        return moviepy_backend
    return None

def _clear_chunks(tmp_dir: Path, format: str) -> None:
    for f in tmp_dir.glob(f"chunk_*.{format}"):
        try:
            f.unlink()
        except OSError:
            pass

def _check_path_traversal(path: Path, base_dir: Path) -> None:
    try:
        resolved_path = Path(path).resolve()
        resolved_base = Path(base_dir).resolve()
        if not str(resolved_path).startswith(str(resolved_base)):
            raise ValueError(f"Path traversal detected: {path} is not under {base_dir}")
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Invalid path verification: {exc}")

def prepare_audio_for_provider(
    input_path: Path | str,
    capabilities: any,  # ProviderCapabilities
    tmp_dir: Path | str,
    *,
    config: any = None,  # AudioConfig
    runner: Callable[[list[str]], object] | None = None,
) -> PreparedAudio:
    input_path = Path(input_path)
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if config is None:
        from app.audio.config import AudioConfig
        config = AudioConfig.from_env()

    # Total duration (probe it)
    try:
        from app.audio.probe import probe_audio
        duration = probe_audio(input_path, runner=runner).duration_seconds
    except Exception:
        duration = 0.0

    target_mb = capabilities.max_upload_mb
    limit_bytes = target_mb * 1024 * 1024

    plan = plan_compression(
        input_path=input_path,
        output_dir=tmp_dir,
        target_mb=target_mb,
        sample_rate=config.target_sample_rate,
        channels=config.target_channels,
        preferred_format=capabilities.preferred_format,
        fallback_format="mp3",
        bitrate=f"{config.target_bitrate // 1000}k" if isinstance(config.target_bitrate, int) else "24k",
        allow_chunking=capabilities.supports_chunking,
    )

    if not getattr(config, "enabled", True):
        input_size_bytes = input_path.stat().st_size if input_path.exists() else 0
        if input_size_bytes > limit_bytes:
            if capabilities.provider == "groq":
                user_msg = "No free tier do Groq, cada upload deve ficar abaixo de 25 MB. O sistema tentará compactar e dividir automaticamente."
            else:
                user_msg = "O arquivo é grande demais para este provedor. Ative compressão/chunking ou escolha Deepgram/local."
            raise ProviderFileTooLargeError(
                f"Audio preparation failed: preprocessing is disabled and file exceeds limit {target_mb} MB",
                provider=capabilities.provider,
                user_message=user_msg,
            )
        return PreparedAudio(
            files=[input_path],
            total_duration_seconds=duration,
            was_compressed=False,
            was_chunked=False,
            format=input_path.suffix.lstrip("."),
            warnings=[],
        )

    input_size_bytes = input_path.stat().st_size if input_path.exists() else 0
    if input_size_bytes <= limit_bytes:
        backend_name = "no-op"
    elif runner is not None:
        backend_name = "ffmpeg_cli"
    else:
        backend_name = select_backend(plan)
    input_size_mb = input_size_bytes / (1024 * 1024)

    if backend_name == "no-op":
        LOGGER.info(
            "Audio preparation: input_size_mb=%.2f, output_size_mb=%.2f, target_mb=%d, backend=%s, duration_seconds=%.2f",
            input_size_mb,
            input_size_mb,
            target_mb,
            backend_name,
            duration,
        )
        return PreparedAudio(
            files=[input_path],
            total_duration_seconds=duration,
            was_compressed=False,
            was_chunked=False,
            format=input_path.suffix.lstrip("."),
            warnings=[],
        )

    backend = _get_backend(backend_name)
    if backend is None:
        raise AudioError(f"Backend {backend_name} could not be resolved.")

    last_exc = None

    # 1. Try compressing/converting to preferred format
    preferred_format = capabilities.preferred_format
    preferred_path = tmp_dir / f"compressed.{preferred_format}"
    _check_path_traversal(preferred_path, tmp_dir)

    try:
        backend.compress(
            input_path=input_path,
            output_path=preferred_path,
            output_dir=tmp_dir,
            sample_rate=plan.sample_rate,
            channels=plan.channels,
            format=preferred_format,
            bitrate=plan.bitrate,
            runner=runner,
        )
    except Exception as exc:
        last_exc = exc
        LOGGER.warning("Preferred format compression (%s) failed: %s", preferred_format, exc)

    output_size_bytes = preferred_path.stat().st_size if preferred_path.exists() else 0
    if output_size_bytes > 0 and output_size_bytes <= limit_bytes:
        output_size_mb = output_size_bytes / (1024 * 1024)
        LOGGER.info(
            "Audio preparation: input_size_mb=%.2f, output_size_mb=%.2f, target_mb=%d, backend=%s, duration_seconds=%.2f",
            input_size_mb,
            output_size_mb,
            target_mb,
            backend_name,
            duration,
        )
        return PreparedAudio(
            files=[preferred_path],
            total_duration_seconds=duration,
            was_compressed=True,
            was_chunked=False,
            format=preferred_format,
            warnings=[],
        )

    # 2. Try fallback (mp3) if preferred is too large or failed
    fallback_format = "mp3"
    fallback_path = tmp_dir / f"compressed.{fallback_format}"
    _check_path_traversal(fallback_path, tmp_dir)

    try:
        backend.compress(
            input_path=input_path,
            output_path=fallback_path,
            output_dir=tmp_dir,
            sample_rate=plan.sample_rate,
            channels=plan.channels,
            format=fallback_format,
            bitrate=plan.bitrate,
            runner=runner,
        )
    except Exception as exc:
        last_exc = exc
        LOGGER.warning("Fallback format compression (mp3) failed: %s", exc)

    output_size_bytes = fallback_path.stat().st_size if fallback_path.exists() else 0
    if output_size_bytes > 0 and output_size_bytes <= limit_bytes:
        output_size_mb = output_size_bytes / (1024 * 1024)
        LOGGER.info(
            "Audio preparation: input_size_mb=%.2f, output_size_mb=%.2f, target_mb=%d, backend=%s, duration_seconds=%.2f",
            input_size_mb,
            output_size_mb,
            target_mb,
            backend_name,
            duration,
        )
        return PreparedAudio(
            files=[fallback_path],
            total_duration_seconds=duration,
            was_compressed=True,
            was_chunked=False,
            format=fallback_format,
            warnings=[],
        )

    # 3. Try chunking if allowed
    if plan.allow_chunking:
        # We slice from the fallback_path if it exists and is valid, otherwise from input_path
        chunk_source = fallback_path if (fallback_path.exists() and fallback_path.stat().st_size > 0) else input_path

        bitrates_to_try = [plan.bitrate, "16k", "8k"]
        chunk_durations_to_try = [
            config.chunk_max_duration_seconds,
            max(60, config.chunk_max_duration_seconds // 2),
            max(60, config.chunk_max_duration_seconds // 4),
        ]

        final_chunks = []
        final_format = fallback_format
        success = False

        for duration_sec in chunk_durations_to_try:
            for br in bitrates_to_try:
                _clear_chunks(tmp_dir, final_format)
                try:
                    chunks = backend.chunk(
                        input_path=chunk_source,
                        output_dir=tmp_dir,
                        segment_time_seconds=duration_sec,
                        format=final_format,
                        bitrate=br,
                        runner=runner,
                    )
                    all_ok = True
                    for c in chunks:
                        if c.stat().st_size > limit_bytes:
                            all_ok = False
                            break
                    if all_ok and chunks:
                        final_chunks = chunks
                        success = True
                        break
                except Exception as exc:
                    last_exc = exc
                    LOGGER.warning("Chunking attempt failed (duration=%d, bitrate=%s): %s", duration_sec, br, exc)
            if success:
                break

        if success:
            total_chunk_size = sum(c.stat().st_size for c in final_chunks)
            output_size_mb = total_chunk_size / (1024 * 1024)
            LOGGER.info(
                "Audio preparation: input_size_mb=%.2f, output_size_mb=%.2f, target_mb=%d, backend=%s, duration_seconds=%.2f",
                input_size_mb,
                output_size_mb,
                target_mb,
                backend_name,
                duration,
            )
            return PreparedAudio(
                files=final_chunks,
                total_duration_seconds=duration,
                was_compressed=True,
                was_chunked=True,
                format=final_format,
                warnings=[],
            )

    if last_exc is not None:
        raise last_exc

    # Raise ProviderFileTooLargeError
    if capabilities.provider == "groq":
        user_msg = "No free tier do Groq, cada upload deve ficar abaixo de 25 MB. O sistema tentará compactar e dividir automaticamente."
    else:
        user_msg = "O arquivo é grande demais para este provedor. Ative compressão/chunking ou escolha Deepgram/local."
    raise ProviderFileTooLargeError(
        f"Audio preparation failed: output exceeds limit {target_mb} MB after compression/chunking",
        provider=capabilities.provider,
        user_message=user_msg,
    )
