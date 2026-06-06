"""Provider error types, re-exported from the central :mod:`app.errors`.

The canonical hierarchy lives in ``app/errors.py`` (the single place that maps a
failure to a friendly ``user_message``). This module simply re-exports the
provider subset so the transcription package can import them from a local path,
matching the provider-layer layout, without risking an import cycle.
"""

from __future__ import annotations

from app.errors import (
    PROVIDER_DOCS_URL,
    ProviderCredentialInvalidError,
    ProviderCredentialMissingError,
    ProviderError,
    ProviderFileTooLargeError,
    ProviderModelUnsupportedError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderUnavailableError,
)

__all__ = [
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
]
