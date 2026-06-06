from __future__ import annotations

import os

import pytest

from app.models.errors import UnknownModelError
from app.models.manifest import (
    WHISPER_CPP_HF_REPO,
    ModelSpec,
    faster_whisper_repo,
    resolve_spec,
    whisper_cpp_download_url,
    whisper_cpp_filename,
)
from app.transcription.config import TranscriptionConfig


def _cfg(**over):
    base = dict(
        enabled=True,
        engine="whisper-cpp",
        model="small",
        language="auto",
        threads=4,
        model_dir="/models",
        compute_type="int8",
        quantization="q4_0",
        model_path=None,
        whisper_cpp_binary="/usr/local/bin/whisper-cli",
        auto_download=False,
        doc_url="https://example/doc",
    )
    base.update(over)
    return TranscriptionConfig(**base)


# --- whisper_cpp_filename ---------------------------------------------------


def test_whisper_cpp_filename_with_quantization():
    assert whisper_cpp_filename("small", "q4_0") == "ggml-small-q4_0.bin"


def test_whisper_cpp_filename_quantization_none():
    assert whisper_cpp_filename("small", None) == "ggml-small.bin"


def test_whisper_cpp_filename_quantization_empty_string():
    assert whisper_cpp_filename("base", "") == "ggml-base.bin"


def test_whisper_cpp_filename_other_model():
    assert whisper_cpp_filename("large-v3", "q5_1") == "ggml-large-v3-q5_1.bin"


def test_whisper_cpp_filename_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        whisper_cpp_filename("huge", "q4_0")


def test_whisper_cpp_filename_unknown_quantization_raises():
    with pytest.raises(UnknownModelError):
        whisper_cpp_filename("small", "q2_k")


# --- whisper_cpp_download_url -----------------------------------------------


def test_whisper_cpp_download_url():
    url = whisper_cpp_download_url("small", "q4_0")
    assert url == (
        f"https://huggingface.co/{WHISPER_CPP_HF_REPO}/resolve/main/"
        "ggml-small-q4_0.bin"
    )


def test_whisper_cpp_download_url_no_quantization():
    url = whisper_cpp_download_url("tiny", None)
    assert url.endswith("/ggml-tiny.bin")


def test_whisper_cpp_download_url_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        whisper_cpp_download_url("nope", None)


# --- faster_whisper_repo ----------------------------------------------------


def test_faster_whisper_repo():
    assert faster_whisper_repo("small") == "Systran/faster-whisper-small"


def test_faster_whisper_repo_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        faster_whisper_repo("huge")


# --- resolve_spec -----------------------------------------------------------


def test_resolve_spec_whisper_cpp_uses_model_dir():
    spec = resolve_spec(_cfg(engine="whisper-cpp", model="small", quantization="q4_0"))
    assert isinstance(spec, ModelSpec)
    assert spec.engine == "whisper-cpp"
    assert spec.model == "small"
    assert spec.quantization == "q4_0"
    assert spec.filename == "ggml-small-q4_0.bin"
    assert spec.download_url == whisper_cpp_download_url("small", "q4_0")
    assert spec.repo is None


def test_resolve_spec_whisper_cpp_respects_model_path():
    cfg = _cfg(engine="whisper-cpp", model_path="/custom/model.bin")
    spec = resolve_spec(cfg)
    # filename/download_url still resolved from manifest, repo stays None
    assert spec.filename == "ggml-small-q4_0.bin"
    assert spec.repo is None


def test_resolve_spec_faster_whisper():
    cfg = _cfg(engine="faster-whisper", model="medium")
    spec = resolve_spec(cfg)
    assert spec.engine == "faster-whisper"
    assert spec.model == "medium"
    assert spec.repo == "Systran/faster-whisper-medium"
    assert spec.filename is None
    assert spec.download_url is None


def test_resolve_spec_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        resolve_spec(_cfg(model="huge"))
