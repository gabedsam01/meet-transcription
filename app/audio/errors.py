"""Audio preprocessing error hierarchy.

Mirrors :mod:`app.errors`: every failure is an :class:`~app.errors.AppError`
carrying a technical message (``str(exc)``, for logs) and a short, friendly,
**secret-free** ``user_message`` (pt-BR) safe to show in the UI. Tracebacks and
ffmpeg/ffprobe stderr stay in logs only — never in ``user_message``.
"""

from __future__ import annotations

from app.errors import AppError


class AudioError(AppError):
    """Base class for audio preprocessing failures."""

    default_user_message = "Não foi possível preparar o áudio da reunião."


class NoAudioTrackError(AudioError):
    """The media file has no audio stream to transcribe."""

    default_user_message = "O arquivo enviado não contém faixa de áudio."


class AudioProbeError(AudioError):
    """ffprobe failed or returned output we could not parse."""

    default_user_message = "Não foi possível analisar o áudio da reunião."


class FfmpegError(AudioError):
    """ffmpeg failed while extracting, compressing, or splitting audio."""

    default_user_message = "Falha ao processar o áudio da reunião."


__all__ = [
    "AudioError",
    "NoAudioTrackError",
    "AudioProbeError",
    "FfmpegError",
]
