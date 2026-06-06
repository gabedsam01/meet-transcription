"""Application error hierarchy.

Every domain failure is an :class:`AppError` carrying THREE things:

- the exception text (``str(exc)``) — technical, for logs/tracebacks;
- ``user_message`` — a short, friendly, **secret-free** sentence safe to show in
  the UI; and
- a stable ``code`` (machine-readable), a ``retryable`` flag, and an optional
  ``doc_url`` — used by structured logging (``app/observability``) and the
  friendly-error UI component (error code + suggested action + docs link).

The worker stores ``user_message`` as a job's ``error_message`` (never a
traceback), so the UI always shows something actionable. Stack traces stay in the
logs only. ``code``/``retryable`` are class-level metadata (no schema change), so
they are available wherever the exception is caught.
"""

from __future__ import annotations

# External docs base; the friendly-error UI links here for self-service fixes.
DOCS_BASE = "https://github.com/gabedsam01/meet-transcription/blob/main/documentation"


class AppError(RuntimeError):
    """Base class for all expected, mapped application failures."""

    default_user_message = "Ocorreu um erro inesperado. Tente novamente."
    #: Stable, machine-readable error code (logs, webhooks, UI).
    code = "app_error"
    #: Whether retrying the same action could plausibly succeed.
    retryable = False
    #: Optional documentation link offered as a self-service fix in the UI.
    doc_url: str | None = None

    def __init__(self, message: str | None = None, *, user_message: str | None = None) -> None:
        self.user_message = user_message or self.default_user_message
        super().__init__(message or self.user_message)


# --- transcription providers ------------------------------------------------


class TranscriptionProviderError(AppError):
    default_user_message = "Não foi possível transcrever a reunião."
    code = "transcription_failed"
    retryable = True


class LocalTranscriptionUnavailableError(TranscriptionProviderError):
    """Local transcription is disabled or invalid AND no Deepgram key is set."""

    default_user_message = (
        "Transcrição indisponível: configure uma Deepgram API Key ou um modelo "
        "local válido."
    )
    code = "local_transcription_unavailable"
    retryable = False
    doc_url = f"{DOCS_BASE}/06-local-transcription.md"


class DeepgramKeyRequiredError(TranscriptionProviderError):
    default_user_message = (
        "Configure sua Deepgram API Key antes de iniciar uma transcrição."
    )
    code = "deepgram_key_required"
    retryable = False
    doc_url = f"{DOCS_BASE}/05-deepgram.md"


class LocalTranscriptionConfigError(TranscriptionProviderError):
    """The local engine configuration is invalid; the worker requires Deepgram."""

    default_user_message = (
        "Modelo local inválido. Consulte a documentação de modelos locais."
    )
    code = "local_transcription_invalid"
    retryable = False
    doc_url = f"{DOCS_BASE}/06-local-transcription.md"


class ModelNotFoundError(LocalTranscriptionConfigError):
    default_user_message = (
        "Arquivo de modelo local não encontrado. Verifique LOCAL_TRANSCRIPTION_MODEL_PATH."
    )
    code = "local_model_not_found"
    doc_url = f"{DOCS_BASE}/07-faster-whisper.md"


class WhisperCppBinaryNotFoundError(LocalTranscriptionConfigError):
    default_user_message = (
        "Binário whisper.cpp não encontrado. Verifique WHISPER_CPP_BINARY."
    )
    code = "whisper_cpp_binary_missing"
    doc_url = f"{DOCS_BASE}/08-whisper-cpp.md"


# --- queue / locking --------------------------------------------------------


class QueueUnavailableError(AppError):
    default_user_message = (
        "Fila indisponível no momento. Tente novamente em instantes."
    )
    code = "queue_unavailable"
    retryable = True
    doc_url = f"{DOCS_BASE}/09-redis-queue.md"


class QueueLockError(AppError):
    default_user_message = "Não foi possível obter o lock de execução."
    code = "queue_lock_failed"
    retryable = True
    doc_url = f"{DOCS_BASE}/09-redis-queue.md"


# --- preconditions ----------------------------------------------------------


class GoogleTokenMissingError(AppError):
    default_user_message = "Conecte sua conta Google antes de transcrever."
    code = "google_not_connected"
    retryable = False
    doc_url = f"{DOCS_BASE}/04-google-oauth.md"


class DriveFolderMissingError(AppError):
    default_user_message = "Configure a pasta de origem no Drive antes de transcrever."
    code = "drive_folder_missing"
    retryable = False
    doc_url = f"{DOCS_BASE}/12-web-ui.md"


class JobAlreadyQueuedError(AppError):
    default_user_message = "Esta transcrição já está na fila."
    code = "job_already_queued"
    retryable = False


class JobAlreadyProcessingError(AppError):
    default_user_message = "Esta transcrição já está em processamento."
    code = "job_already_processing"
    retryable = False


def error_code(exc: BaseException) -> str:
    """Stable code for any exception (``AppError.code`` or a generic fallback)."""
    return exc.code if isinstance(exc, AppError) else "internal_error"


def is_retryable(exc: BaseException) -> bool:
    """Whether retrying is plausible; unknown (non-AppError) failures are not."""
    return bool(getattr(exc, "retryable", False))


__all__ = [
    "DOCS_BASE",
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
    "error_code",
    "is_retryable",
]
