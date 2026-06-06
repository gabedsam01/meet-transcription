"""The provider registry catalogues providers, models and capabilities."""

from app.transcription import provider_models as pm


def test_known_providers_present():
    ids = set(pm.provider_ids())
    assert {"deepgram", "openrouter", "gemini", "groq", "assemblyai", "local"} <= ids
    assert pm.CLOUD_PROVIDERS == ("deepgram", "openrouter", "gemini", "groq", "assemblyai")


def test_deepgram_models_and_real_diarization():
    spec = pm.get_provider_spec("deepgram")
    assert spec.models == ("nova-3", "nova-2", "whisper")
    assert spec.default_model == "nova-3"
    assert spec.requires_api_key is True
    assert spec.diarization_kind == pm.DIARIZATION_REAL


def test_openrouter_models_listed():
    spec = pm.get_provider_spec("openrouter")
    assert "openai/whisper-large-v3-turbo" in spec.models
    assert "mistralai/voxtral-mini-transcribe" in spec.models
    assert spec.diarization_kind == pm.DIARIZATION_MODEL_DEPENDENT


def test_gemini_models_and_size_limits():
    spec = pm.get_provider_spec("gemini")
    assert "gemini-2.5-flash" in spec.models
    assert spec.diarization_kind == pm.DIARIZATION_PSEUDO
    assert spec.max_inline_bytes == pm.GEMINI_INLINE_MAX_BYTES == 70 * 1024 * 1024
    assert spec.max_file_bytes == pm.GEMINI_FILES_MAX_BYTES == 99 * 1024 * 1024


def test_groq_spec_and_dynamic_limits():
    import os
    spec = pm.get_provider_spec("groq")
    assert spec.models == ("whisper-large-v3-turbo", "whisper-large-v3")
    assert spec.default_model == "whisper-large-v3-turbo"
    assert spec.requires_api_key is True
    assert spec.diarization_kind == pm.DIARIZATION_NONE

    # Test free-tier limit default (25 MB)
    orig_dev = os.environ.get("GROQ_USE_DEV_LIMIT")
    orig_max = os.environ.get("GROQ_MAX_UPLOAD_MB")
    if "GROQ_MAX_UPLOAD_MB" in os.environ:
        del os.environ["GROQ_MAX_UPLOAD_MB"]
    if "GROQ_USE_DEV_LIMIT" in os.environ:
        del os.environ["GROQ_USE_DEV_LIMIT"]
    try:
        assert spec.max_file_bytes == 25 * 1024 * 1024

        # Test dev limit overrides (100 MB)
        os.environ["GROQ_USE_DEV_LIMIT"] = "true"
        assert spec.max_file_bytes == 100 * 1024 * 1024
        del os.environ["GROQ_USE_DEV_LIMIT"]

        # Test custom max upload mb override (e.g. 50 MB)
        os.environ["GROQ_MAX_UPLOAD_MB"] = "50"
        assert spec.max_file_bytes == 50 * 1024 * 1024
    finally:
        if orig_dev is not None:
            os.environ["GROQ_USE_DEV_LIMIT"] = orig_dev
        elif "GROQ_USE_DEV_LIMIT" in os.environ:
            del os.environ["GROQ_USE_DEV_LIMIT"]
        if orig_max is not None:
            os.environ["GROQ_MAX_UPLOAD_MB"] = orig_max
        elif "GROQ_MAX_UPLOAD_MB" in os.environ:
            del os.environ["GROQ_MAX_UPLOAD_MB"]


def test_local_needs_no_api_key():
    assert pm.requires_api_key("local") is False
    assert pm.requires_api_key("deepgram") is True


def test_validity_helpers():
    assert pm.is_valid_provider("openrouter") is True
    assert pm.is_valid_provider("nope") is False
    assert pm.is_valid_model("deepgram", "nova-3") is True
    assert pm.is_valid_model("deepgram", "gpt-9") is False
    assert pm.is_cloud_provider("local") is False
    assert pm.default_model("gemini") == "gemini-2.5-flash"
    assert pm.models_for("nope") == ()


def test_assemblyai_spec():
    spec = pm.get_provider_spec("assemblyai")
    assert spec is not None
    assert spec.provider_id == "assemblyai"
    assert spec.models == ("universal-3-pro", "universal-2")
    assert spec.default_model == "universal-3-pro"
    assert spec.requires_api_key is True
    assert spec.diarization_kind == pm.DIARIZATION_REAL
    assert spec.max_file_bytes == 99 * 1024 * 1024
