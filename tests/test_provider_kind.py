from app.transcription.provider_kind import (
    CLOUD,
    LOCAL,
    classify_provider_kind,
)


def test_cloud_providers_classify_as_cloud():
    for name in ("deepgram", "gemini", "openrouter"):
        assert classify_provider_kind(name) == CLOUD


def test_local_providers_classify_as_local():
    for name in ("faster-whisper", "whisper-cpp"):
        assert classify_provider_kind(name) == LOCAL


def test_classification_is_case_insensitive():
    assert classify_provider_kind("Faster-Whisper") == LOCAL
    assert classify_provider_kind("DEEPGRAM") == CLOUD


def test_unknown_provider_defaults_to_cloud():
    # A chosen cloud SaaS we have not catalogued must not accidentally serialize as
    # a CPU-bound local job. Unknown -> cloud (the safe side to overcommit).
    assert classify_provider_kind("some-new-saas") == CLOUD


def test_none_or_blank_defaults_to_cloud():
    assert classify_provider_kind(None) == CLOUD
    assert classify_provider_kind("  ") == CLOUD


def test_providers_expose_name():
    from app.transcription.deepgram_provider import DeepgramProvider
    from app.transcription.faster_whisper_provider import FasterWhisperProvider
    from app.transcription.whisper_cpp_provider import WhisperCppProvider

    assert DeepgramProvider.name == "deepgram"
    assert FasterWhisperProvider.name == "faster-whisper"
    assert WhisperCppProvider.name == "whisper-cpp"
