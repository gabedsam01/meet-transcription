from __future__ import annotations

from app.errors import AppError

# Diarization failures. As with every AppError, the technical detail lives in
# ``str(exc)`` (logs only) and ``user_message`` is the short, secret-free pt-BR
# sentence shown in the UI. The pyannote auth token must NEVER appear in either
# the message or the user_message.


class DiarizationError(AppError):
    default_user_message = (
        "Não foi possível identificar os participantes (diarização)."
    )


class DiarizationUnavailableError(DiarizationError):
    """Diarization is enabled but the engine/token is missing or invalid."""

    default_user_message = (
        "Diarização indisponível: verifique o engine e o token de acesso."
    )


class DiarizationModelError(DiarizationError):
    """The diarization model/pipeline could not be loaded or run."""

    default_user_message = "Falha ao carregar o modelo de diarização."


__all__ = [
    "DiarizationError",
    "DiarizationUnavailableError",
    "DiarizationModelError",
]
