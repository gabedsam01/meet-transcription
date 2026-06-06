import pytest

from app.summaries import (
    NullSummaryProvider,
    SummaryProvider,
    SummarySettings,
    SummaryUnavailableError,
    build_summary_provider,
    get_summary_status,
)


def test_disabled_by_default():
    settings = SummarySettings.from_env({})
    assert settings.enabled is False
    assert settings.provider == "none"
    assert get_summary_status(settings).enabled is False


def test_configured_provider_not_yet_available():
    settings = SummarySettings.from_env(
        {"SUMMARY_ENABLED": "true", "SUMMARY_PROVIDER": "claude", "SUMMARY_MODEL": "x"}
    )
    status = get_summary_status(settings)
    # Enabled in config, but the integration is not shipped in this release.
    assert status.enabled is False
    assert status.provider == "claude"


def test_null_provider_satisfies_protocol_and_raises_friendly():
    provider = build_summary_provider()
    assert isinstance(provider, NullSummaryProvider)
    assert isinstance(provider, SummaryProvider)
    with pytest.raises(SummaryUnavailableError):
        provider.summarize("qualquer transcrição")
