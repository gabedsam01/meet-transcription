"""Tests for the ``model-init`` entrypoint (app.model_init.main).

These never download: they drive ``main`` through the environment, pointing
whisper.cpp at an on-disk file (present) or nowhere (absent, auto-download off).
"""

from app.model_init import main


def _base_env(monkeypatch, **over):
    # A clean-ish env: only the keys main() reads via TranscriptionConfig.from_env.
    for key in (
        "LOCAL_TRANSCRIPTION_ENABLED", "LOCAL_TRANSCRIPTION_ENGINE",
        "LOCAL_TRANSCRIPTION_MODEL", "LOCAL_TRANSCRIPTION_QUANTIZATION",
        "LOCAL_TRANSCRIPTION_MODEL_PATH", "LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD",
        "WHISPER_CPP_BINARY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in over.items():
        monkeypatch.setenv(key, value)


def test_disabled_is_a_noop_exit_zero(monkeypatch):
    _base_env(monkeypatch, LOCAL_TRANSCRIPTION_ENABLED="false")
    assert main() == 0


def test_whisper_cpp_ready_when_model_present(monkeypatch, tmp_path):
    model = tmp_path / "ggml-small-q4_0.bin"
    model.write_bytes(b"ggml")
    _base_env(
        monkeypatch,
        LOCAL_TRANSCRIPTION_ENABLED="true",
        LOCAL_TRANSCRIPTION_ENGINE="whisper-cpp",
        LOCAL_TRANSCRIPTION_MODEL="small",
        LOCAL_TRANSCRIPTION_QUANTIZATION="q4_0",
        LOCAL_TRANSCRIPTION_MODEL_PATH=str(model),
    )
    assert main() == 0


def test_whisper_cpp_not_ready_when_missing_and_no_auto_download(monkeypatch, tmp_path):
    _base_env(
        monkeypatch,
        LOCAL_TRANSCRIPTION_ENABLED="true",
        LOCAL_TRANSCRIPTION_ENGINE="whisper-cpp",
        LOCAL_TRANSCRIPTION_MODEL="small",
        LOCAL_TRANSCRIPTION_QUANTIZATION="q4_0",
        LOCAL_TRANSCRIPTION_MODEL_PATH=str(tmp_path / "absent.bin"),
        LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD="false",
    )
    assert main() == 2
