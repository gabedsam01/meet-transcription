from __future__ import annotations

import pytest

from app.diarization.align import diarize_and_align
from app.diarization.config import DiarizationConfig
from app.diarization.errors import (
    DiarizationError,
    DiarizationModelError,
    DiarizationUnavailableError,
)
from app.diarization.none_provider import NoneDiarizationProvider
from app.diarization.provider import (
    DiarizationProbes,
    DiarizationProvider,
    DiarizationStatus,
    SpeakerTurn,
    build_diarization_provider,
    get_diarization_status,
)
from app.errors import AppError


def _probes(available: bool) -> DiarizationProbes:
    return DiarizationProbes(module_available=lambda name: available)


# --- config -----------------------------------------------------------------


def test_config_defaults_disabled():
    config = DiarizationConfig.from_env({})
    assert config.enabled is False
    assert config.engine == "none"
    assert config.model == "pyannote/speaker-diarization-3.1"
    assert config.auth_token is None
    assert config.required is False
    assert config.min_speakers is None
    assert config.max_speakers is None


def test_config_disabled_classmethod():
    config = DiarizationConfig.disabled()
    assert config.enabled is False


def test_config_from_env_reads_values():
    config = DiarizationConfig.from_env(
        {
            "DIARIZATION_ENABLED": "true",
            "DIARIZATION_ENGINE": "Pyannote",
            "DIARIZATION_MODEL": "pyannote/speaker-diarization-3.1",
            "DIARIZATION_AUTH_TOKEN": "hf_secret",
            "DIARIZATION_REQUIRED": "true",
            "DIARIZATION_MIN_SPEAKERS": "2",
            "DIARIZATION_MAX_SPEAKERS": "5",
        }
    )
    assert config.enabled is True
    assert config.engine == "pyannote"  # lowercased
    assert config.auth_token == "hf_secret"
    assert config.required is True
    assert config.min_speakers == 2
    assert config.max_speakers == 5


def test_config_never_raises_on_bad_values():
    config = DiarizationConfig.from_env(
        {
            "DIARIZATION_ENABLED": "not-a-bool",
            "DIARIZATION_MIN_SPEAKERS": "abc",
            "DIARIZATION_MAX_SPEAKERS": "",
        }
    )
    assert config.enabled is False  # bad bool -> safe default
    assert config.min_speakers is None
    assert config.max_speakers is None


def test_config_empty_auth_token_is_none():
    config = DiarizationConfig.from_env({"DIARIZATION_AUTH_TOKEN": "   "})
    assert config.auth_token is None


# --- status -----------------------------------------------------------------


def test_status_disabled():
    config = DiarizationConfig.disabled()
    status = get_diarization_status(config, probes=_probes(True))
    assert status.enabled is False
    assert status.valid is False
    assert status.message == "Diarização desativada."


def test_status_engine_none_while_enabled_is_noop():
    config = DiarizationConfig.from_env(
        {"DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "none"}
    )
    status = get_diarization_status(config, probes=_probes(True))
    assert status.enabled is True
    assert status.valid is False
    assert status.message == "Diarização desativada."


def test_status_pyannote_valid():
    config = DiarizationConfig.from_env(
        {
            "DIARIZATION_ENABLED": "true",
            "DIARIZATION_ENGINE": "pyannote",
            "DIARIZATION_AUTH_TOKEN": "hf_token",
        }
    )
    status = get_diarization_status(config, probes=_probes(True))
    assert status.enabled is True
    assert status.valid is True
    assert status.engine == "pyannote"


def test_status_pyannote_invalid_when_module_missing():
    config = DiarizationConfig.from_env(
        {
            "DIARIZATION_ENABLED": "true",
            "DIARIZATION_ENGINE": "pyannote",
            "DIARIZATION_AUTH_TOKEN": "hf_token",
        }
    )
    status = get_diarization_status(config, probes=_probes(False))
    assert status.valid is False
    assert status.reason is not None


def test_status_pyannote_invalid_when_token_missing():
    config = DiarizationConfig.from_env(
        {"DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "pyannote"}
    )
    status = get_diarization_status(config, probes=_probes(True))
    assert status.valid is False
    assert status.reason is not None


def test_default_probes_handle_missing_pyannote_without_raising():
    # In this environment pyannote.audio is NOT installed; the real probe must
    # return a falsy status (not raise) so worker startup never crashes.
    config = DiarizationConfig.from_env(
        {
            "DIARIZATION_ENABLED": "true",
            "DIARIZATION_ENGINE": "pyannote",
            "DIARIZATION_AUTH_TOKEN": "hf_token",
        }
    )
    status = get_diarization_status(config)  # default probes
    assert status.valid is False


def test_status_unknown_engine_invalid():
    config = DiarizationConfig.from_env(
        {"DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "bogus"}
    )
    status = get_diarization_status(config, probes=_probes(True))
    assert status.valid is False


# --- provider construction --------------------------------------------------


def test_build_provider_returns_none_provider_when_disabled():
    config = DiarizationConfig.disabled()
    provider = build_diarization_provider(config)
    assert isinstance(provider, NoneDiarizationProvider)
    assert provider.diarize("audio.wav") == []


def test_build_provider_returns_none_provider_for_engine_none():
    config = DiarizationConfig.from_env(
        {"DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "none"}
    )
    provider = build_diarization_provider(config)
    assert isinstance(provider, NoneDiarizationProvider)


def test_build_provider_returns_pyannote_for_engine_pyannote():
    from app.diarization.pyannote_provider import PyannoteDiarizationProvider

    config = DiarizationConfig.from_env(
        {"DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "pyannote"}
    )
    provider = build_diarization_provider(config)
    assert isinstance(provider, PyannoteDiarizationProvider)


def test_none_provider_satisfies_protocol():
    assert isinstance(NoneDiarizationProvider(), DiarizationProvider)


# --- diarize_and_align ------------------------------------------------------


class _FakeProvider:
    def __init__(self, turns):
        self._turns = turns
        self.calls = []

    def diarize(self, audio_path, *, min_speakers=None, max_speakers=None):
        self.calls.append((audio_path, min_speakers, max_speakers))
        return list(self._turns)


def test_diarize_and_align_disabled_returns_segments_unchanged():
    config = DiarizationConfig.disabled()
    segments = [{"start": 0.0, "end": 1.0, "speaker": None, "text": "oi"}]
    out, status = diarize_and_align(config, "a.wav", segments, probes=_probes(True))
    assert status.valid is False
    assert out == segments
    assert all(s["speaker"] is None for s in out)


def test_diarize_and_align_assigns_speakers_with_mock_provider():
    config = DiarizationConfig.from_env(
        {
            "DIARIZATION_ENABLED": "true",
            "DIARIZATION_ENGINE": "pyannote",
            "DIARIZATION_AUTH_TOKEN": "hf_token",
            "DIARIZATION_MIN_SPEAKERS": "1",
            "DIARIZATION_MAX_SPEAKERS": "3",
        }
    )
    turns = [
        SpeakerTurn(0.0, 2.0, "SPEAKER_00"),
        SpeakerTurn(2.0, 4.0, "SPEAKER_01"),
    ]
    provider = _FakeProvider(turns)
    segments = [
        {"start": 0.0, "end": 1.5, "speaker": None, "text": "a"},
        {"start": 2.1, "end": 3.9, "speaker": None, "text": "b"},
    ]
    out, status = diarize_and_align(
        config, "audio.wav", segments, provider=provider, probes=_probes(True)
    )
    assert status.valid is True
    assert out[0]["speaker"] == "SPEAKER_00"
    assert out[1]["speaker"] == "SPEAKER_01"
    # min/max speakers forwarded to the provider
    assert provider.calls == [("audio.wav", 1, 3)]
    # original segments not mutated
    assert segments[0]["speaker"] is None


def test_diarize_and_align_invalid_skips_provider_call():
    config = DiarizationConfig.from_env(
        {"DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "pyannote"}
    )  # no token -> invalid
    provider = _FakeProvider([SpeakerTurn(0.0, 1.0, "SPEAKER_00")])
    segments = [{"start": 0.0, "end": 1.0, "speaker": None, "text": "x"}]
    out, status = diarize_and_align(
        config, "a.wav", segments, provider=provider, probes=_probes(True)
    )
    assert status.valid is False
    assert provider.calls == []
    assert out == segments


# --- errors -----------------------------------------------------------------


def test_error_hierarchy():
    assert issubclass(DiarizationError, AppError)
    assert issubclass(DiarizationUnavailableError, DiarizationError)
    assert issubclass(DiarizationModelError, DiarizationError)


def test_errors_have_ptbr_user_messages():
    assert DiarizationError().user_message == (
        "Não foi possível identificar os participantes (diarização)."
    )
    assert DiarizationUnavailableError().user_message == (
        "Diarização indisponível: verifique o engine e o token de acesso."
    )
    assert DiarizationModelError().user_message == (
        "Falha ao carregar o modelo de diarização."
    )


def test_auth_token_never_in_model_error_message():
    secret = "hf_super_secret_token_value"
    config = DiarizationConfig.from_env(
        {
            "DIARIZATION_ENABLED": "true",
            "DIARIZATION_ENGINE": "pyannote",
            "DIARIZATION_AUTH_TOKEN": secret,
        }
    )
    from app.diarization.pyannote_provider import PyannoteDiarizationProvider

    def exploding_factory():
        raise RuntimeError("boom loading model")

    provider = PyannoteDiarizationProvider(config, pipeline_factory=exploding_factory)
    with pytest.raises(DiarizationModelError) as excinfo:
        provider.diarize("audio.wav")
    err = excinfo.value
    assert secret not in str(err)
    assert secret not in err.user_message
