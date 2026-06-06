"""Cloud provider resolution: turn a user's ModelSettings into a built provider.

``resolve_cloud_provider`` decides which provider actually runs: the primary if
its credential is present, otherwise the configured fallback, otherwise a
friendly ``ProviderError`` that names the provider and links the docs. It is a
pure decision function — the actual provider object is produced by the injected
``build`` callback, so the resolver itself imports no SDK and is trivially
testable.

This is distinct from ``app.transcription.factory.resolve_provider``, which owns
the orthogonal *local-engine vs Deepgram* product rule driven by the environment.
The worker consults the local rule first; only when a user has explicitly chosen
a cloud provider in the Models tab does this resolver take over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from app.errors import (
    ProviderCredentialMissingError,
    ProviderError,
    ProviderModelUnsupportedError,
    ProviderNotConfiguredError,
)
from app.transcription.provider import TranscriptionProvider
from app.transcription.provider_config import ModelSettings
from app.transcription.provider_models import (
    CLOUD_PROVIDERS,
    ProviderSpec,
    get_provider_spec,
)

# build(provider_id, model, api_key) -> provider
CloudBuilder = Callable[[str, str, str | None], TranscriptionProvider]


@dataclass(frozen=True)
class ResolvedProvider:
    provider: TranscriptionProvider
    provider_id: str
    model: str
    is_fallback: bool
    spec: ProviderSpec

    @property
    def label(self) -> str:
        suffix = " (fallback)" if self.is_fallback else ""
        return f"{self.provider_id}:{self.model}{suffix}"


def resolve_cloud_provider(
    settings: ModelSettings,
    credentials: Mapping[str, str | None] | None,
    *,
    build: CloudBuilder,
) -> ResolvedProvider:
    """Resolve the provider to run, applying the fallback rule.

    Tries the primary provider; if it cannot be built (typically a missing key)
    and a fallback is configured, tries the fallback. If neither works, the
    primary's error is raised so the message stays actionable for the user.
    """
    creds = credentials or {}
    primary_id = settings.primary_provider
    if not primary_id or primary_id not in CLOUD_PROVIDERS:
        raise ProviderNotConfiguredError(
            f"No usable primary provider configured ({primary_id!r})",
            provider=primary_id or None,
        )
    try:
        return _build_one(
            primary_id, settings.primary_model, creds, build, is_fallback=False
        )
    except ProviderError as primary_error:
        if settings.has_fallback:
            try:
                return _build_one(
                    settings.fallback_provider,
                    settings.fallback_model,
                    creds,
                    build,
                    is_fallback=True,
                )
            except ProviderError:
                # Fallback also failed — surface the primary's friendly error.
                pass
        raise primary_error


def _build_one(
    provider_id: str | None,
    model: str | None,
    credentials: Mapping[str, str | None],
    build: CloudBuilder,
    *,
    is_fallback: bool,
) -> ResolvedProvider:
    spec = get_provider_spec(provider_id)
    if spec is None or provider_id not in CLOUD_PROVIDERS:
        raise ProviderNotConfiguredError(
            f"Unknown provider {provider_id!r}", provider=provider_id
        )
    chosen_model = model if (model and spec.has_model(model)) else spec.default_model
    if not chosen_model:
        raise ProviderModelUnsupportedError(
            f"No usable model for provider {provider_id!r}", provider=provider_id
        )
    api_key: str | None = None
    if spec.requires_api_key:
        api_key = (credentials.get(provider_id) or "").strip() or None
        if not api_key:
            raise ProviderCredentialMissingError(
                f"Missing API key for provider {provider_id!r}",
                user_message=(
                    f"Falta a API key do provedor {spec.label}. Configure-a na aba Models."
                ),
                provider=provider_id,
                docs_url=spec.docs_url,
            )
    provider = build(provider_id, chosen_model, api_key)
    return ResolvedProvider(
        provider=provider,
        provider_id=provider_id,
        model=chosen_model,
        is_fallback=is_fallback,
        spec=spec,
    )


class ProviderResolver:
    """Object wrapper around :func:`resolve_cloud_provider` for DI-style callers."""

    def __init__(self, build: CloudBuilder) -> None:
        self._build = build

    def resolve(
        self,
        settings: ModelSettings,
        credentials: Mapping[str, str | None] | None,
    ) -> ResolvedProvider:
        return resolve_cloud_provider(settings, credentials, build=self._build)


__all__ = [
    "CloudBuilder",
    "ResolvedProvider",
    "ProviderResolver",
    "resolve_cloud_provider",
]
