"""ModelSettings normalization clamps invalid choices to safe defaults."""

from app.transcription.provider_config import (
    DEFAULT_PRIMARY_PROVIDER,
    ModelSettings,
    default_model_settings,
    normalize_model_settings,
    with_fallback,
    with_primary,
)


def test_default_is_deepgram_nova3():
    ms = default_model_settings()
    assert ms.primary_provider == DEFAULT_PRIMARY_PROVIDER == "deepgram"
    assert ms.primary_model == "nova-3"
    assert ms.fallback_enabled is False


def test_unknown_provider_falls_back_to_default():
    ms = normalize_model_settings(primary_provider="bogus", primary_model="x")
    assert ms.primary_provider == "deepgram"
    assert ms.primary_model == "nova-3"


def test_unknown_model_for_valid_provider_uses_default_model():
    ms = normalize_model_settings(primary_provider="gemini", primary_model="not-a-model")
    assert ms.primary_provider == "gemini"
    assert ms.primary_model == "gemini-2.5-flash"


def test_valid_choice_preserved():
    ms = normalize_model_settings(
        primary_provider="openrouter", primary_model="openai/whisper-large-v3"
    )
    assert ms.primary_provider == "openrouter"
    assert ms.primary_model == "openai/whisper-large-v3"


def test_fallback_requires_full_spec_and_distinct_provider():
    # Fallback identical to primary is dropped.
    ms = normalize_model_settings(
        primary_provider="openrouter",
        primary_model="openai/whisper-large-v3",
        fallback_enabled=True,
        fallback_provider="openrouter",
        fallback_model="openai/whisper-large-v3-turbo",
    )
    assert ms.fallback_enabled is False
    assert ms.fallback_provider is None

    # Distinct, fully-specified fallback is kept.
    ms2 = normalize_model_settings(
        primary_provider="openrouter",
        primary_model="openai/whisper-large-v3",
        fallback_enabled=True,
        fallback_provider="deepgram",
        fallback_model="nova-3",
    )
    assert ms2.has_fallback is True
    assert ms2.fallback_provider == "deepgram"
    assert ms2.fallback_model == "nova-3"


def test_fallback_model_defaulted_when_missing():
    ms = normalize_model_settings(
        primary_provider="deepgram",
        primary_model="nova-3",
        fallback_enabled=True,
        fallback_provider="gemini",
        fallback_model=None,
    )
    assert ms.fallback_provider == "gemini"
    assert ms.fallback_model == "gemini-2.5-flash"


def test_with_primary_and_with_fallback_helpers():
    ms = default_model_settings()
    ms = with_primary(ms, "gemini", "gemini-3.5-flash")
    assert ms.primary_provider == "gemini" and ms.primary_model == "gemini-3.5-flash"
    ms = with_fallback(ms, enabled=True, provider="deepgram", model="nova-2")
    assert ms.has_fallback and ms.fallback_provider == "deepgram"
    ms = with_fallback(ms, enabled=False, provider="deepgram", model="nova-2")
    assert ms.fallback_enabled is False


def test_model_settings_is_frozen():
    ms = ModelSettings(primary_provider="deepgram", primary_model="nova-3")
    try:
        ms.primary_provider = "gemini"  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        assert "frozen" in str(exc).lower() or isinstance(exc, AttributeError)
    else:
        raise AssertionError("ModelSettings should be immutable")
