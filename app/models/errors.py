"""Errors raised while preparing a local transcription model.

Every class subclasses :class:`app.errors.AppError`, so each carries a
``default_user_message`` — a short, friendly, secret-free pt-BR sentence safe to
show in the UI. Technical detail (model name, path, underlying cause) goes in the
exception text (``str(exc)``), never in ``user_message``.
"""

from __future__ import annotations

from app.errors import AppError


class ModelManagerError(AppError):
    """Base for any failure while preparing the local model."""

    default_user_message = "Não foi possível preparar o modelo local de transcrição."


class UnknownModelError(ModelManagerError):
    """The requested engine/model/quantization is not in the allowed-lists."""

    default_user_message = "Modelo local não suportado."


class ModelDownloadError(ModelManagerError):
    """The model download failed or the file was missing afterwards."""

    default_user_message = (
        "Falha ao baixar o modelo local. Verifique a conexão e o espaço em disco."
    )


class AutoDownloadDisabledError(ModelManagerError):
    """The model is absent and automatic download is turned off."""

    default_user_message = (
        "Modelo local ausente e download automático desativado "
        "(LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD)."
    )


__all__ = [
    "ModelManagerError",
    "UnknownModelError",
    "ModelDownloadError",
    "AutoDownloadDisabledError",
]
