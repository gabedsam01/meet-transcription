"""Per-user model selection (``user_model_settings``) as a domain object.

``ModelSettings`` is the immutable choice a user makes in the Models tab: a
primary provider+model, an optional fallback provider+model, and the local-engine
overrides. ``normalize_model_settings`` clamps anything invalid back to safe
defaults so a stale or hand-edited row can never crash the worker — the same
defensive posture as ``TranscriptionConfig.from_env``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.transcription.provider_models import (
    DEEPGRAM,
    default_model,
    is_valid_model,
    is_valid_provider,
)

# A brand-new user with no row yet defaults to Deepgram (the historical provider).
DEFAULT_PRIMARY_PROVIDER = DEEPGRAM


@dataclass(frozen=True)
class ModelSettings:
    primary_provider: str
    primary_model: str
    fallback_enabled: bool = False
    fallback_provider: str | None = None
    fallback_model: str | None = None
    local_engine: str | None = None
    local_model: str | None = None
    local_quantization: str | None = None

    @property
    def has_fallback(self) -> bool:
        return bool(
            self.fallback_enabled
            and self.fallback_provider
            and self.fallback_model
        )


def default_model_settings(provider: str | None = None) -> ModelSettings:
    provider = provider if is_valid_provider(provider) else DEFAULT_PRIMARY_PROVIDER
    return ModelSettings(
        primary_provider=provider,
        primary_model=default_model(provider) or "",
    )


def normalize_model_settings(
    *,
    primary_provider: str | None,
    primary_model: str | None = None,
    fallback_enabled: bool = False,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
    local_engine: str | None = None,
    local_model: str | None = None,
    local_quantization: str | None = None,
) -> ModelSettings:
    """Build a valid ModelSettings, repairing invalid provider/model choices.

    Unknown providers fall back to the default; an unknown model for a valid
    provider falls back to that provider's default model. Fallback is only kept
    when it is fully specified *and* points at a different provider than primary.
    """
    provider = primary_provider if is_valid_provider(primary_provider) else DEFAULT_PRIMARY_PROVIDER
    model = primary_model if is_valid_model(provider, primary_model) else default_model(provider)

    fb_provider = fallback_provider if is_valid_provider(fallback_provider) else None
    fb_model = (
        fallback_model
        if (fb_provider and is_valid_model(fb_provider, fallback_model))
        else (default_model(fb_provider) if fb_provider else None)
    )
    # A fallback identical to the primary provider is pointless — drop it.
    if fb_provider == provider:
        fb_provider = None
        fb_model = None
    enabled = bool(fallback_enabled and fb_provider and fb_model)

    return ModelSettings(
        primary_provider=provider,
        primary_model=model or "",
        fallback_enabled=enabled,
        fallback_provider=fb_provider if enabled else None,
        fallback_model=fb_model if enabled else None,
        local_engine=(local_engine or None),
        local_model=(local_model or None),
        local_quantization=(local_quantization or None),
    )


def with_primary(settings: ModelSettings, provider: str, model: str | None) -> ModelSettings:
    """Return a copy with a new, normalized primary provider/model."""
    return normalize_model_settings(
        primary_provider=provider,
        primary_model=model,
        fallback_enabled=settings.fallback_enabled,
        fallback_provider=settings.fallback_provider,
        fallback_model=settings.fallback_model,
        local_engine=settings.local_engine,
        local_model=settings.local_model,
        local_quantization=settings.local_quantization,
    )


def with_fallback(
    settings: ModelSettings,
    *,
    enabled: bool,
    provider: str | None,
    model: str | None,
) -> ModelSettings:
    """Return a copy with a new, normalized fallback configuration."""
    return normalize_model_settings(
        primary_provider=settings.primary_provider,
        primary_model=settings.primary_model,
        fallback_enabled=enabled,
        fallback_provider=provider,
        fallback_model=model,
        local_engine=settings.local_engine,
        local_model=settings.local_model,
        local_quantization=settings.local_quantization,
    )


__all__ = [
    "ModelSettings",
    "DEFAULT_PRIMARY_PROVIDER",
    "default_model_settings",
    "normalize_model_settings",
    "with_primary",
    "with_fallback",
]
