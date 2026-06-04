from app.transcription.config import TranscriptionConfig
from app.transcription.local_validation import ValidationProbes, validate_local_config


def _cfg(**over):
    base = dict(
        enabled=True,
        engine="faster-whisper",
        model="small",
        language="auto",
        threads=4,
        model_dir="/models",
        compute_type="int8",
        quantization="q4_0",
        model_path=None,
        whisper_cpp_binary=None,
        auto_download=False,
        doc_url="https://example/doc",
    )
    base.update(over)
    return TranscriptionConfig(**base)


def _probes(*, module=True, path=True, executable=True):
    return ValidationProbes(
        module_available=lambda name: module,
        path_exists=lambda p: path,
        is_executable=lambda p: executable,
    )


# --- faster-whisper ---------------------------------------------------------


def test_faster_whisper_valid_summary():
    result = validate_local_config(_cfg(), _probes(module=True))
    assert result.valid is True
    assert result.summary == "faster-whisper small int8"


def test_faster_whisper_invalid_model():
    result = validate_local_config(_cfg(model="huge"), _probes(module=True))
    assert result.valid is False
    assert "huge" in result.reason


def test_faster_whisper_invalid_compute_type():
    result = validate_local_config(_cfg(compute_type="float16"), _probes(module=True))
    assert result.valid is False


def test_faster_whisper_package_not_installed_is_invalid():
    result = validate_local_config(_cfg(), _probes(module=False))
    assert result.valid is False
    assert "faster-whisper" in result.reason


# --- whisper.cpp ------------------------------------------------------------


def _wcpp(**over):
    base = dict(
        engine="whisper-cpp",
        quantization="q4_0",
        model_path="/models/ggml-small-q4_0.bin",
        whisper_cpp_binary="/usr/local/bin/whisper-cli",
    )
    base.update(over)
    return _cfg(**base)


def test_whisper_cpp_valid_summary():
    result = validate_local_config(_wcpp(), _probes(path=True, executable=True))
    assert result.valid is True
    assert result.summary == "whisper.cpp small q4_0"


def test_whisper_cpp_invalid_quantization():
    result = validate_local_config(_wcpp(quantization="q2_k"), _probes())
    assert result.valid is False


def test_whisper_cpp_missing_binary():
    result = validate_local_config(_wcpp(), _probes(executable=False))
    assert result.valid is False
    assert "whisper" in result.reason.lower()


def test_whisper_cpp_missing_model_path_without_auto_download():
    result = validate_local_config(_wcpp(), _probes(path=False))
    assert result.valid is False


def test_whisper_cpp_requires_model_path_even_with_auto_download():
    # whisper.cpp cannot auto-download a ggml model, so model_path is ALWAYS
    # required; auto_download only meaningfully applies to faster-whisper.
    result = validate_local_config(
        _wcpp(auto_download=True), _probes(path=False, executable=True)
    )
    assert result.valid is False


# --- unknown engine ---------------------------------------------------------


def test_unknown_engine_is_invalid():
    result = validate_local_config(_cfg(engine="vosk"), _probes())
    assert result.valid is False
    assert "vosk" in result.reason
