"""The provider registry catalogues providers, models and capabilities."""

from app.transcription import provider_models as pm


def test_known_providers_present():
    ids = set(pm.provider_ids())
    assert {"deepgram", "openrouter", "gemini", "local"} <= ids
    assert pm.CLOUD_PROVIDERS == ("deepgram", "openrouter", "gemini")


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
