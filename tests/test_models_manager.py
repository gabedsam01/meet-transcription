from __future__ import annotations

import os

import pytest

from app.models.downloader import (
    download_faster_whisper_model,
    download_whisper_cpp_model,
)
from app.models.errors import (
    AutoDownloadDisabledError,
    ModelDownloadError,
    UnknownModelError,
)
from app.models.manager import ModelStatus, ensure_model
from app.models.validators import (
    expected_whisper_cpp_path,
    faster_whisper_model_present,
    whisper_cpp_model_present,
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


# --- validators -------------------------------------------------------------


def test_expected_whisper_cpp_path_from_model_dir():
    cfg = _cfg(model_dir="/models", model="small", quantization="q4_0")
    assert expected_whisper_cpp_path(cfg) == os.path.join(
        "/models", "ggml-small-q4_0.bin"
    )


def test_expected_whisper_cpp_path_prefers_model_path():
    cfg = _cfg(model_path="/custom/m.bin")
    assert expected_whisper_cpp_path(cfg) == "/custom/m.bin"


def test_whisper_cpp_model_present_with_model_path():
    cfg = _cfg(model_path="/custom/m.bin")
    assert whisper_cpp_model_present(cfg, path_exists=lambda p: p == "/custom/m.bin")


def test_whisper_cpp_model_present_in_model_dir():
    cfg = _cfg(model_dir="/models")
    target = os.path.join("/models", "ggml-small-q4_0.bin")
    assert whisper_cpp_model_present(cfg, path_exists=lambda p: p == target)


def test_whisper_cpp_model_absent():
    cfg = _cfg()
    assert not whisper_cpp_model_present(cfg, path_exists=lambda p: False)


def test_faster_whisper_model_present():
    cfg = _cfg(engine="faster-whisper", model="small", model_dir="/cache")
    snapshot = os.path.join("/cache", "models--Systran--faster-whisper-small")
    assert faster_whisper_model_present(cfg, path_exists=lambda p: p == snapshot)


def test_faster_whisper_model_absent():
    cfg = _cfg(engine="faster-whisper")
    assert not faster_whisper_model_present(cfg, path_exists=lambda p: False)


# --- downloader -------------------------------------------------------------


def test_download_whisper_cpp_calls_fetcher(tmp_path):
    dest_dir = tmp_path / "models"
    cfg = _cfg(model_dir=str(dest_dir), auto_download=True)
    calls = {}

    def fetcher(url, dest):
        calls["url"] = url
        calls["dest"] = dest
        # simulate a successful download by creating the file
        with open(dest, "w") as fh:
            fh.write("ggml")

    result = download_whisper_cpp_model(cfg, fetcher=fetcher)
    expected = str(dest_dir / "ggml-small-q4_0.bin")
    assert result == expected
    assert calls["dest"] == expected
    assert "ggml-small-q4_0.bin" in calls["url"]
    assert os.path.exists(expected)


def test_download_whisper_cpp_disabled_raises(tmp_path):
    cfg = _cfg(model_dir=str(tmp_path), auto_download=False)
    called = {"n": 0}

    def fetcher(url, dest):
        called["n"] += 1

    with pytest.raises(AutoDownloadDisabledError):
        download_whisper_cpp_model(cfg, fetcher=fetcher)
    assert called["n"] == 0


def test_download_whisper_cpp_missing_after_fetch_raises(tmp_path):
    cfg = _cfg(model_dir=str(tmp_path), auto_download=True)

    def fetcher(url, dest):
        # does NOT create the file
        return None

    with pytest.raises(ModelDownloadError):
        download_whisper_cpp_model(cfg, fetcher=fetcher)


def test_download_faster_whisper_calls_downloader(tmp_path):
    cfg = _cfg(engine="faster-whisper", model="small", model_dir=str(tmp_path), auto_download=True)
    seen = {}

    def downloader(repo, cache_dir):
        seen["repo"] = repo
        seen["cache_dir"] = cache_dir
        return "/cache/models--Systran--faster-whisper-small/snapshot"

    result = download_faster_whisper_model(cfg, downloader=downloader)
    assert seen["repo"] == "Systran/faster-whisper-small"
    assert seen["cache_dir"] == str(tmp_path)
    assert result == "/cache/models--Systran--faster-whisper-small/snapshot"


def test_download_faster_whisper_disabled_raises(tmp_path):
    cfg = _cfg(engine="faster-whisper", model_dir=str(tmp_path), auto_download=False)
    called = {"n": 0}

    def downloader(repo, cache_dir):
        called["n"] += 1
        return "x"

    with pytest.raises(AutoDownloadDisabledError):
        download_faster_whisper_model(cfg, downloader=downloader)
    assert called["n"] == 0


# --- ensure_model -----------------------------------------------------------


def test_ensure_model_disabled_noop():
    cfg = _cfg(enabled=False)
    fetcher_called = {"n": 0}

    status = ensure_model(
        cfg, fetcher=lambda *a: fetcher_called.__setitem__("n", 1)
    )
    assert isinstance(status, ModelStatus)
    assert status.ready is True
    assert status.path is None
    assert fetcher_called["n"] == 0


def test_ensure_model_unknown_engine_raises():
    with pytest.raises(UnknownModelError):
        ensure_model(_cfg(engine="vosk"))


def test_ensure_model_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        ensure_model(_cfg(model="huge"))


def test_ensure_model_whisper_cpp_present_no_download():
    cfg = _cfg(model_dir="/models")
    target = expected_whisper_cpp_path(cfg)
    fetcher_called = {"n": 0}

    def fetcher(url, dest):
        fetcher_called["n"] += 1

    status = ensure_model(
        cfg, fetcher=fetcher, path_exists=lambda p: p == target
    )
    assert status.ready is True
    assert status.path == target
    assert fetcher_called["n"] == 0


def test_ensure_model_whisper_cpp_missing_with_auto_download_calls_fetcher(tmp_path):
    cfg = _cfg(model_dir=str(tmp_path), auto_download=True)
    target = expected_whisper_cpp_path(cfg)
    state = {"downloaded": False}

    def fetcher(url, dest):
        state["downloaded"] = True
        with open(dest, "w") as fh:
            fh.write("ggml")

    def path_exists(p):
        # absent until the fetcher writes the real file on disk
        if p == target:
            return os.path.exists(p)
        return False

    status = ensure_model(cfg, fetcher=fetcher, path_exists=path_exists)
    assert state["downloaded"] is True
    assert status.ready is True
    assert status.path == target


def test_ensure_model_whisper_cpp_missing_no_auto_download_not_ready():
    cfg = _cfg(model_dir="/models", auto_download=False)
    fetcher_called = {"n": 0}

    def fetcher(url, dest):
        fetcher_called["n"] += 1

    status = ensure_model(cfg, fetcher=fetcher, path_exists=lambda p: False)
    assert status.ready is False
    assert status.reason is not None
    assert fetcher_called["n"] == 0


def test_ensure_model_faster_whisper_present_no_download():
    cfg = _cfg(engine="faster-whisper", model="small", model_dir="/cache")
    snapshot = os.path.join("/cache", "models--Systran--faster-whisper-small")
    fw_called = {"n": 0}

    def fw_downloader(repo, cache_dir):
        fw_called["n"] += 1
        return "x"

    status = ensure_model(
        cfg, fw_downloader=fw_downloader, path_exists=lambda p: p == snapshot
    )
    assert status.ready is True
    assert fw_called["n"] == 0


def test_ensure_model_faster_whisper_missing_with_auto_download():
    cfg = _cfg(engine="faster-whisper", model="small", model_dir="/cache", auto_download=True)
    fw_called = {"n": 0}

    def fw_downloader(repo, cache_dir):
        fw_called["n"] += 1
        return "/cache/snap"

    status = ensure_model(
        cfg, fw_downloader=fw_downloader, path_exists=lambda p: False
    )
    assert status.ready is True
    assert fw_called["n"] == 1


def test_ensure_model_faster_whisper_missing_no_auto_download_not_ready():
    cfg = _cfg(engine="faster-whisper", model="small", model_dir="/cache", auto_download=False)
    fw_called = {"n": 0}

    def fw_downloader(repo, cache_dir):
        fw_called["n"] += 1
        return "x"

    status = ensure_model(
        cfg, fw_downloader=fw_downloader, path_exists=lambda p: False
    )
    assert status.ready is False
    assert fw_called["n"] == 0
