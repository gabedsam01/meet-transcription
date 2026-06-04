import pytest

from app.transcription.config import TranscriptionConfig
from app.transcription.factory import (
    LocalTranscriptionUnavailable,
    build_local_provider,
    resolve_provider,
)
from app.transcription.faster_whisper_provider import FasterWhisperProvider
from app.transcription.local_validation import ValidationProbes
from app.transcription.whisper_cpp_provider import WhisperCppProvider


def _local_cfg(**over):
    env = {"LOCAL_TRANSCRIPTION_ENABLED": "true", "LOCAL_TRANSCRIPTION_ENGINE": "faster-whisper"}
    env.update(over)
    return TranscriptionConfig.from_env(env)


def _probes(module=True):
    return ValidationProbes(
        module_available=lambda name: module,
        path_exists=lambda p: True,
        is_executable=lambda p: True,
    )


_LOCAL = object()
_DEEPGRAM = object()


def _build_local(cfg):
    return _LOCAL


def _build_deepgram():
    return _DEEPGRAM


# --- factory ---------------------------------------------------------------


def test_build_local_provider_selects_engine():
    assert isinstance(
        build_local_provider(_local_cfg(LOCAL_TRANSCRIPTION_ENGINE="faster-whisper")),
        FasterWhisperProvider,
    )
    assert isinstance(
        build_local_provider(
            _local_cfg(
                LOCAL_TRANSCRIPTION_ENGINE="whisper-cpp",
                WHISPER_CPP_BINARY="/usr/local/bin/whisper-cli",
            )
        ),
        WhisperCppProvider,
    )


# --- resolve_provider ------------------------------------------------------


def test_resolves_local_when_valid_without_requiring_deepgram():
    provider, status = resolve_provider(
        _local_cfg(),
        has_deepgram_key=False,
        build_local_provider=_build_local,
        build_deepgram_provider=_build_deepgram,
        probes=_probes(module=True),
    )
    assert provider is _LOCAL
    assert status.local_valid is True


def test_resolves_deepgram_when_local_disabled():
    provider, status = resolve_provider(
        TranscriptionConfig.disabled(),
        has_deepgram_key=True,
        build_local_provider=_build_local,
        build_deepgram_provider=_build_deepgram,
        probes=_probes(),
    )
    assert provider is _DEEPGRAM
    assert status.deepgram_required is True


def test_disabled_without_key_raises_mentioning_deepgram():
    with pytest.raises(LocalTranscriptionUnavailable, match="Deepgram"):
        resolve_provider(
            TranscriptionConfig.disabled(),
            has_deepgram_key=False,
            build_local_provider=_build_local,
            build_deepgram_provider=_build_deepgram,
            probes=_probes(),
        )


def test_invalid_local_without_key_raises_with_doc_url():
    cfg = _local_cfg(LOCAL_TRANSCRIPTION_DOC_URL="https://docs/local")
    with pytest.raises(LocalTranscriptionUnavailable) as exc:
        resolve_provider(
            cfg,
            has_deepgram_key=False,
            build_local_provider=_build_local,
            build_deepgram_provider=_build_deepgram,
            probes=_probes(module=False),  # faster-whisper not installed -> invalid
        )
    assert "Deepgram" in str(exc.value)
    assert "https://docs/local" in str(exc.value)


def test_invalid_local_with_key_falls_back_to_deepgram():
    provider, status = resolve_provider(
        _local_cfg(),
        has_deepgram_key=True,
        build_local_provider=_build_local,
        build_deepgram_provider=_build_deepgram,
        probes=_probes(module=False),
    )
    assert provider is _DEEPGRAM
    assert status.local_valid is False
