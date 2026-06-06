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


# --- cloud provider registry (Deepgram / OpenRouter / Gemini) ---------------
#
# These carry richer diagnostics than the base AppError so the Models tab can
# render an actionable message and a docs link without ever leaking a key or a
# traceback. Every instance exposes ``code``, ``user_message`` (friendly, shown
# in the UI), ``technical_message`` (logs only), ``retryable`` and ``docs_url``.

PROVIDER_DOCS_URL = (
    "https://github.com/gabedsam01/meet-transcription/blob/main/"
    "documentation/21-provider-registry.md"
)


class ProviderError(TranscriptionProviderError):
    """Base for cloud-provider failures with structured, secret-free metadata."""

    code: str = "provider_error"
    retryable: bool = False
    docs_url: str | None = PROVIDER_DOCS_URL
    default_user_message = "Não foi possível transcrever com o provedor selecionado."

    def __init__(
        self,
        message: str | None = None,
        *,
        user_message: str | None = None,
        docs_url: str | None = None,
        retryable: bool | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.provider = provider
        if docs_url is not None:
            self.docs_url = docs_url
        if retryable is not None:
            self.retryable = retryable

    @property
    def technical_message(self) -> str:
        """The exception text — for logs/diagnostics, never shown in the UI."""
        return str(self)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "user_message": self.user_message,
            "technical_message": self.technical_message,
            "retryable": self.retryable,
            "docs_url": self.docs_url,
            "provider": self.provider,
        }


class ProviderNotConfiguredError(ProviderError):
    code = "provider_not_configured"
    default_user_message = (
        "Nenhum provedor de transcrição configurado. Escolha um provedor na aba Models."
    )


class ProviderCredentialMissingError(ProviderError):
    code = "provider_credential_missing"
    default_user_message = (
        "Falta a API key do provedor selecionado. Configure-a na aba Models."
    )


class ProviderCredentialInvalidError(ProviderError):
    code = "provider_credential_invalid"
    default_user_message = (
        "A API key do provedor é inválida ou não tem permissão. Verifique a chave na aba Models."
    )


class ProviderRateLimitedError(ProviderError):
    code = "provider_rate_limited"
    retryable = True
    default_user_message = (
        "O provedor está limitando as requisições (rate limit). Tente novamente em instantes."
    )


class ProviderFileTooLargeError(ProviderError):
    code = "provider_file_too_large"
    default_user_message = (
        "O arquivo é grande demais para o provedor selecionado. Use um arquivo menor "
        "ou outro provedor."
    )


class ProviderResponseError(ProviderError):
    code = "provider_response_error"
    default_user_message = (
        "O provedor retornou uma resposta inesperada. Tente novamente ou use outro provedor."
    )


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"
    retryable = True
    default_user_message = (
        "O provedor está indisponível no momento. Tente novamente em instantes."
    )


class ProviderModelUnsupportedError(ProviderError):
    code = "provider_model_unsupported"
    default_user_message = (
        "O modelo selecionado não é suportado por este provedor. Escolha outro modelo na aba Models."
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


__all__ = [
    "AppError",
    "TranscriptionProviderError",
    "LocalTranscriptionUnavailableError",
    "DeepgramKeyRequiredError",
    "LocalTranscriptionConfigError",
    "ModelNotFoundError",
    "WhisperCppBinaryNotFoundError",
    "PROVIDER_DOCS_URL",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderCredentialMissingError",
    "ProviderCredentialInvalidError",
    "ProviderRateLimitedError",
    "ProviderFileTooLargeError",
    "ProviderResponseError",
    "ProviderUnavailableError",
    "ProviderModelUnsupportedError",
    "QueueUnavailableError",
    "QueueLockError",
    "GoogleTokenMissingError",
    "DriveFolderMissingError",
    "JobAlreadyQueuedError",
    "JobAlreadyProcessingError",
]
