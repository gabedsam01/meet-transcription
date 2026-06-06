"""Download local whisper models.

The actual fetching (HTTP for whisper.cpp ggml files, ``snapshot_download`` for
faster-whisper) is injected so tests never hit the network. ``huggingface_hub``
is a heavy, optional, build-arg-gated dependency and is imported LAZILY inside
the default downloader only.
"""

from __future__ import annotations

import os
from typing import Callable

from app.models.errors import AutoDownloadDisabledError, ModelDownloadError
from app.models.manifest import (
    faster_whisper_repo,
    whisper_cpp_download_url,
)
from app.models.validators import expected_whisper_cpp_path
from app.transcription.config import TranscriptionConfig

# (url, dest_path) -> downloads url to dest_path.
Fetcher = Callable[[str, str], None]
# (repo, cache_dir) -> local snapshot path.
FasterWhisperDownloader = Callable[[str, str], str]

PathExists = Callable[[str], bool]


def _default_fetcher(url: str, dest: str) -> None:
    from urllib.request import urlretrieve  # lazy: stdlib, no network at import

    urlretrieve(url, dest)  # noqa: S310 - HTTPS HuggingFace resolve URL


def download_whisper_cpp_model(
    config: TranscriptionConfig,
    *,
    fetcher: Fetcher | None = None,
    path_exists: PathExists = os.path.exists,
) -> str:
    """Download the whisper.cpp ggml model to its expected path and return it.

    Raises :class:`AutoDownloadDisabledError` when ``auto_download`` is off (the
    fetcher is never called) and :class:`ModelDownloadError` if the file is still
    missing after the fetcher runs.
    """

    dest = expected_whisper_cpp_path(config)
    if not config.auto_download:
        raise AutoDownloadDisabledError(
            f"Auto-download disabled; whisper.cpp model missing at {dest!r}"
        )

    fetcher = fetcher or _default_fetcher
    url = whisper_cpp_download_url(config.model, config.quantization or None)

    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)

    try:
        fetcher(url, dest)
    except Exception as exc:  # network/IO error -> friendly ModelDownloadError
        raise ModelDownloadError(
            f"Failed to download whisper.cpp model to {dest!r}: {exc}"
        ) from exc

    if not path_exists(dest):
        raise ModelDownloadError(
            f"whisper.cpp model missing after download at {dest!r}"
        )
    return dest


def _default_fw_downloader(repo: str, cache_dir: str) -> str:
    from huggingface_hub import snapshot_download  # lazy: heavy optional dep

    return snapshot_download(repo_id=repo, cache_dir=cache_dir)


def download_faster_whisper_model(
    config: TranscriptionConfig,
    *,
    downloader: FasterWhisperDownloader | None = None,
) -> str:
    """Download a faster-whisper HF snapshot into ``model_dir`` and return its path.

    faster-whisper also auto-downloads at first ``WhisperModel`` use; this is the
    eager/explicit path. Raises :class:`AutoDownloadDisabledError` when
    ``auto_download`` is off (the downloader is never called).
    """

    if not config.auto_download:
        raise AutoDownloadDisabledError(
            f"Auto-download disabled; faster-whisper model {config.model!r} missing"
        )

    downloader = downloader or _default_fw_downloader
    repo = faster_whisper_repo(config.model)

    try:
        return downloader(repo, config.model_dir)
    except Exception as exc:
        raise ModelDownloadError(
            f"Failed to download faster-whisper model {repo!r}: {exc}"
        ) from exc


__all__ = [
    "Fetcher",
    "FasterWhisperDownloader",
    "download_whisper_cpp_model",
    "download_faster_whisper_model",
]
