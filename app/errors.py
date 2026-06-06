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
    """Base class for all expected, mapped application failures.

    ``error_code`` is a stable machine code stored on the job (``last_error_code``)
    and used by the retry policy; ``retryable`` says whether the worker may retry
    with backoff. Defaults are conservative: an unmapped ``AppError`` is treated as
    an unexpected, terminal failure.
    """

    default_user_message = "Ocorreu um erro inesperado. Tente novamente."
    error_code = "UNEXPECTED"
    retryable = False

    def __init__(self, message: str | None = None, *, user_message: str | None = None) -> None:
        self.user_message = user_message or self.default_user_message
        super().__init__(message or self.user_message)


# --- transcription providers ------------------------------------------------


class TranscriptionProviderError(AppError):
    default_user_message = "Não foi possível transcrever a reunião."


class DeepgramRateLimitError(TranscriptionProviderError):
    """Provider returned HTTP 429. Transient — retry with backoff."""

    error_code = "RATE_LIMIT"
    retryable = True
    default_user_message = (
        "Provider está rate-limited. Tentaremos novamente."
    )

    def __init__(
        self,
        message: str | None = None,
        *,
        user_message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.retry_after_seconds = retry_after_seconds


class ProviderKeyInvalidError(TranscriptionProviderError):
    """Provider rejected the API key (HTTP 401/403). Terminal — never retry."""

    error_code = "KEY_INVALID"
    retryable = False
    default_user_message = (
        "Chave de API do provider inválida. Verifique sua configuração."
    )


class FileTooLargeError(TranscriptionProviderError):
    """The media file exceeds the provider's limit (HTTP 413). Terminal."""

    error_code = "FILE_TOO_LARGE"
    retryable = False
    default_user_message = "Arquivo excede o limite permitido pelo provider."


class LocalTranscriptionUnavailableError(TranscriptionProviderError):
    """Local transcription is disabled or invalid AND no Deepgram key is set."""

    error_code = "CONFIG"
    retryable = False
    default_user_message = (
        "Transcrição indisponível: configure uma Deepgram API Key ou um modelo "
        "local válido."
    )


class DeepgramKeyRequiredError(TranscriptionProviderError):
    error_code = "CONFIG"
    retryable = False
    default_user_message = (
        "Configure sua Deepgram API Key antes de iniciar uma transcrição."
    )


class LocalTranscriptionConfigError(TranscriptionProviderError):
    """The local engine configuration is invalid; the worker requires Deepgram."""

    error_code = "CONFIG"
    retryable = False
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


def classify_error(exc: BaseException) -> tuple[str, bool, int | None]:
    """Map an exception to ``(error_code, retryable, retry_after_seconds)``.

    Mapped :class:`AppError`s carry their own classification. Anything else is an
    unexpected error treated as **retryable** (up to the job's max attempts) so a
    transient blip never permanently fails a job before the dead-letter cap.
    """
    if isinstance(exc, AppError):
        return exc.error_code, exc.retryable, getattr(exc, "retry_after_seconds", None)
    return "UNEXPECTED", True, None


__all__ = [
    "AppError",
    "TranscriptionProviderError",
    "DeepgramRateLimitError",
    "ProviderKeyInvalidError",
    "FileTooLargeError",
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
    "classify_error",
]
