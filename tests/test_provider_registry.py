import pytest

from app.errors import ProviderCredentialMissingError, ProviderNotConfiguredError
from app.transcription.provider_config import normalize_model_settings
from app.transcription.registry import (
    ProviderResolver,
    resolve_cloud_provider,
)


class StubProvider:
    def __init__(self, provider_id, model, api_key):
        self.provider_id = provider_id
        self.model = model
        self.api_key = api_key


def _build(provider_id, model, api_key):
    return StubProvider(provider_id, model, api_key)


def test_resolves_primary_when_key_present():
    ms = normalize_model_settings(
        primary_provider="openrouter", primary_model="openai/whisper-large-v3"
    )
    resolved = resolve_cloud_provider(ms, {"openrouter": "or-key"}, build=_build)
    assert resolved.provider_id == "openrouter"
    assert resolved.is_fallback is False
    assert resolved.provider.api_key == "or-key"
    assert resolved.provider.model == "openai/whisper-large-v3"


def test_uses_fallback_when_primary_key_missing():
    ms = normalize_model_settings(
        primary_provider="openrouter",
        primary_model="openai/whisper-large-v3",
        fallback_enabled=True,
        fallback_provider="deepgram",
        fallback_model="nova-3",
    )
    resolved = resolve_cloud_provider(ms, {"deepgram": "dg-key"}, build=_build)
    assert resolved.provider_id == "deepgram"
    assert resolved.is_fallback is True
    assert resolved.provider.api_key == "dg-key"
    assert "fallback" in resolved.label


def test_friendly_error_when_no_key_and_no_fallback():
    ms = normalize_model_settings(
        primary_provider="gemini", primary_model="gemini-2.5-flash"
    )
    with pytest.raises(ProviderCredentialMissingError) as exc:
        resolve_cloud_provider(ms, {}, build=_build)
    # Names the provider, links the docs, no secret.
    assert "Gemini" in exc.value.user_message
    assert exc.value.docs_url
    assert exc.value.provider == "gemini"


def test_primary_error_raised_when_fallback_also_missing_key():
    ms = normalize_model_settings(
        primary_provider="openrouter",
        primary_model="openai/whisper-large-v3",
        fallback_enabled=True,
        fallback_provider="gemini",
        fallback_model="gemini-2.5-flash",
    )
    with pytest.raises(ProviderCredentialMissingError) as exc:
        resolve_cloud_provider(ms, {}, build=_build)
    assert exc.value.provider == "openrouter"  # the primary's error survives


def test_unknown_primary_raises_not_configured():
    # normalize would repair this, so construct a raw settings object directly.
    from app.transcription.provider_config import ModelSettings

    ms = ModelSettings(primary_provider="nope", primary_model="x")
    with pytest.raises(ProviderNotConfiguredError):
        resolve_cloud_provider(ms, {}, build=_build)


def test_invalid_model_clamped_to_default():
    from app.transcription.provider_config import ModelSettings

    ms = ModelSettings(primary_provider="deepgram", primary_model="not-real")
    resolved = resolve_cloud_provider(ms, {"deepgram": "k"}, build=_build)
    assert resolved.model == "nova-3"


def test_provider_resolver_wrapper():
    resolver = ProviderResolver(_build)
    ms = normalize_model_settings(primary_provider="deepgram", primary_model="nova-3")
    resolved = resolver.resolve(ms, {"deepgram": "k"})
    assert resolved.provider_id == "deepgram"
