from app.transcription.config import TranscriptionConfig
from app.transcription.local_validation import ValidationProbes
from app.transcription.provider import get_transcription_provider_status


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


def _probes(module=True):
    return ValidationProbes(
        module_available=lambda name: module,
        path_exists=lambda p: True,
        is_executable=lambda p: True,
    )


def test_disabled_requires_deepgram():
    status = get_transcription_provider_status(
        TranscriptionConfig.disabled(), probes=_probes()
    )
    assert status.local_valid is False
    assert status.deepgram_required is True
    assert "Deepgram" in status.message


def test_valid_local_does_not_require_deepgram():
    status = get_transcription_provider_status(_cfg(), probes=_probes(module=True))
    assert status.local_valid is True
    assert status.deepgram_required is False
    assert status.message == "Modelo local ativo: faster-whisper small int8"


def test_invalid_local_requires_deepgram_and_links_docs():
    status = get_transcription_provider_status(
        _cfg(model="huge"), probes=_probes(module=True)
    )
    assert status.local_valid is False
    assert status.deepgram_required is True
    assert status.message == "Modelo local inválido. Consulte a documentação de modelos locais."
    assert status.doc_url == "https://example/doc"
