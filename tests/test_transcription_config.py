from app.transcription.config import TranscriptionConfig


def test_defaults_are_disabled_with_faster_whisper_small_int8():
    cfg = TranscriptionConfig.from_env({})
    assert cfg.enabled is False
    assert cfg.engine == "faster-whisper"
    assert cfg.model == "small"
    assert cfg.language == "auto"
    assert cfg.threads == 4
    assert cfg.compute_type == "int8"
    assert cfg.quantization == "q4_0"
    assert cfg.model_dir == "/models"
    assert cfg.auto_download is False
    assert "local-transcription" in cfg.doc_url


def test_reads_overrides_and_normalizes_engine_underscore_to_hyphen():
    cfg = TranscriptionConfig.from_env(
        {
            "LOCAL_TRANSCRIPTION_ENABLED": "true",
            "LOCAL_TRANSCRIPTION_ENGINE": "whisper_cpp",
            "LOCAL_TRANSCRIPTION_MODEL": "medium",
            "LOCAL_TRANSCRIPTION_LANGUAGE": "pt",
            "LOCAL_TRANSCRIPTION_THREADS": "8",
            "LOCAL_TRANSCRIPTION_QUANTIZATION": "q5_0",
            "LOCAL_TRANSCRIPTION_MODEL_PATH": "/models/ggml-medium-q5_0.bin",
            "WHISPER_CPP_BINARY": "/usr/local/bin/whisper-cli",
            "LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD": "true",
        }
    )
    assert cfg.enabled is True
    assert cfg.engine == "whisper-cpp"  # underscore normalized to hyphen
    assert cfg.model == "medium"
    assert cfg.language == "pt"
    assert cfg.threads == 8
    assert cfg.quantization == "q5_0"
    assert cfg.model_path == "/models/ggml-medium-q5_0.bin"
    assert cfg.whisper_cpp_binary == "/usr/local/bin/whisper-cli"
    assert cfg.auto_download is True


def test_bad_threads_falls_back_to_default_instead_of_crashing():
    # An env misconfig must never crash worker startup; it degrades to a default.
    cfg = TranscriptionConfig.from_env({"LOCAL_TRANSCRIPTION_THREADS": "not-an-int"})
    assert cfg.threads == 4


def test_disabled_factory():
    cfg = TranscriptionConfig.disabled()
    assert cfg.enabled is False
