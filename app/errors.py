"""Application error hierarchy.

Every domain failure is an :class:`AppError` carrying TWO messages:

- the exception text (``str(exc)``) — technical, for logs/tracebacks; and
- ``user_message`` — a short, friendly, **secret-free** sentence safe to show in
  the UI.

The worker stores ``user_message`` as a job's ``error_message`` (never a
traceback), so the UI always shows something actionable. Stack traces stay in the
logs only.
"""

from __future__ import annotations


class AppError(RuntimeError):
    """Base class for all expected, mapped application failures."""

    default_user_message = "Ocorreu um erro inesperado. Tente novamente."

    def __init__(self, message: str | None = None, *, user_message: str | None = None) -> None:
        self.user_message = user_message or self.default_user_message
        super().__init__(message or self.user_message)


# --- transcription providers ------------------------------------------------


class TranscriptionProviderError(AppError):
    default_user_message = "Não foi possível transcrever a reunião."


class LocalTranscriptionUnavailableError(TranscriptionProviderError):
    """Local transcription is disabled or invalid AND no Deepgram key is set."""

    default_user_message = (
        "Transcrição indisponível: configure uma Deepgram API Key ou um modelo "
        "local válido."
    )


class DeepgramKeyRequiredError(TranscriptionProviderError):
    default_user_message = (
        "Configure sua Deepgram API Key antes de iniciar uma transcrição."
    )


class LocalTranscriptionConfigError(TranscriptionProviderError):
    """The local engine configuration is invalid; the worker requires Deepgram."""

    default_user_message = (
        "Modelo local inválido. Consulte a documentação de modelos locais."
    )


class ModelNotFoundError(LocalTranscriptionConfigError):
    default_user_message = (
        "Arquivo de modelo local não encontrado. Verifique LOCAL_TRANSCRIPTION_MODEL_PATH."
    )


class WhisperCppBinaryNotFoundError(LocalTranscriptionConfigError):
    default_user_message = (
        "Binário whisper.cpp não encontrado. Verifique WHISPER_CPP_BINARY."
    )


# --- queue / locking --------------------------------------------------------


class QueueUnavailableError(AppError):
    default_user_message = (
        "Fila indisponível no momento. Tente novamente em instantes."
    )


class QueueLockError(AppError):
    default_user_message = "Não foi possível obter o lock de execução."


# --- preconditions ----------------------------------------------------------


class GoogleTokenMissingError(AppError):
    default_user_message = "Conecte sua conta Google antes de transcrever."


class DriveFolderMissingError(AppError):
    default_user_message = "Configure a pasta de origem no Drive antes de transcrever."


class JobAlreadyQueuedError(AppError):
    default_user_message = "Esta transcrição já está na fila."


class JobAlreadyProcessingError(AppError):
    default_user_message = "Esta transcrição já está em processamento."


# --- chrome-extension recording uploads -------------------------------------


class RecordingNotFoundError(AppError):
    """An upload job's recording file is missing from the shared recordings dir."""

    default_user_message = (
        "Gravação enviada não encontrada. Reenvie a gravação pela extensão."
    )


__all__ = [
    "AppError",
    "TranscriptionProviderError",
    "LocalTranscriptionUnavailableError",
    "DeepgramKeyRequiredError",
    "LocalTranscriptionConfigError",
    "ModelNotFoundError",
    "WhisperCppBinaryNotFoundError",
    "QueueUnavailableError",
    "QueueLockError",
    "GoogleTokenMissingError",
    "DriveFolderMissingError",
    "JobAlreadyQueuedError",
    "JobAlreadyProcessingError",
    "RecordingNotFoundError",
]
