"""Shared helpers for Chrome-extension recording uploads.

A recording uploaded by the Chrome extension is NOT a Google Drive file, so it
cannot be modeled with a Drive ``source_file_id`` the worker downloads. Instead
the upload endpoint:

1. writes the media + a metadata sidecar to a directory SHARED by web and worker
   (both mount ``./data`` in docker-compose), keyed by an opaque ``recording_id``;
2. creates a pending job whose ``source_file_id`` is the sentinel
   ``chrome-extension:<recording_id>``.

The worker recognizes the sentinel (``is_upload_source``), resolves the local
file (``resolve_recording_file``) and transcribes it directly — never touching
Drive. The media file is written BEFORE the job row exists, so the worker can
never claim a job whose recording is not yet on disk.

PostgreSQL stays the single source of truth for job state; this module only owns
the on-disk recording payload, exactly like the worker's tmp workspace.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

# Marks a job whose media is a locally-uploaded recording. Kept deliberately
# distinct from any Drive id so ``is_upload_source`` is unambiguous.
UPLOAD_SOURCE_PREFIX = "chrome-extension:"
UPLOAD_SOURCE = "chrome-extension"

DEFAULT_RECORDINGS_DIR = "/app/data/recordings"


def recordings_dir_from_env(env: Mapping[str, str] | None = None) -> Path:
    values = env if env is not None else os.environ
    raw = values.get("EXTENSION_RECORDINGS_DIR", "").strip() or DEFAULT_RECORDINGS_DIR
    return Path(raw)


@dataclass(frozen=True)
class RecordingMetadata:
    """Sidecar describing one uploaded recording. Never contains secrets."""

    recording_id: str
    filename: str  # stored basename, e.g. "<recording_id>.webm"
    source: str = UPLOAD_SOURCE
    meeting_url: str | None = None
    meeting_title: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    content_type: str | None = None


def new_recording_id() -> str:
    return uuid.uuid4().hex


def source_file_id_for(recording_id: str) -> str:
    return f"{UPLOAD_SOURCE_PREFIX}{recording_id}"


def is_upload_source(source_file_id: str | None) -> bool:
    return bool(source_file_id) and source_file_id.startswith(UPLOAD_SOURCE_PREFIX)


def recording_id_from_source(source_file_id: str) -> str:
    return source_file_id[len(UPLOAD_SOURCE_PREFIX):]


def recording_path(recordings_dir: str | Path, recording_id: str, suffix: str = ".webm") -> Path:
    return Path(recordings_dir) / f"{recording_id}{suffix}"


def metadata_path(recordings_dir: str | Path, recording_id: str) -> Path:
    return Path(recordings_dir) / f"{recording_id}.json"


def write_metadata(recordings_dir: str | Path, meta: RecordingMetadata) -> Path:
    path = metadata_path(recordings_dir, meta.recording_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def read_metadata(recordings_dir: str | Path, recording_id: str) -> RecordingMetadata | None:
    path = metadata_path(recordings_dir, recording_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # Tolerate unknown keys from a newer writer without crashing the worker.
    fields = RecordingMetadata.__dataclass_fields__
    return RecordingMetadata(**{k: v for k, v in data.items() if k in fields})


def resolve_recording_file(recordings_dir: str | Path, recording_id: str) -> Path | None:
    """Locate the stored media for ``recording_id``.

    Prefers the basename recorded in the sidecar; falls back to globbing
    ``<recording_id>.*`` (skipping the ``.json`` sidecar) so a missing/partial
    sidecar never strands a present recording.
    """
    meta = read_metadata(recordings_dir, recording_id)
    # Defense-in-depth: only trust the sidecar filename if it is a bare basename
    # (no path separators), so a tampered sidecar can never escape recordings_dir.
    if meta is not None and meta.filename and Path(meta.filename).name == meta.filename:
        candidate = Path(recordings_dir) / meta.filename
        if candidate.exists():
            return candidate
    for path in sorted(Path(recordings_dir).glob(f"{recording_id}.*")):
        if path.suffix.lower() != ".json":
            return path
    return None


def cleanup_recording(recordings_dir: str | Path, recording_id: str) -> None:
    """Best-effort removal of a recording + sidecar after a job reaches a terminal
    state. Never raises (a leftover file is harmless; PostgreSQL owns job state)."""
    for path in Path(recordings_dir).glob(f"{recording_id}.*"):
        try:
            path.unlink()
        except OSError:
            pass
