import json
from pathlib import Path

from app.recordings import (
    RecordingMetadata,
    cleanup_recording,
    is_upload_source,
    new_recording_id,
    read_metadata,
    recording_id_from_source,
    recording_path,
    recordings_dir_from_env,
    resolve_recording_file,
    source_file_id_for,
    write_metadata,
)


def test_source_sentinel_round_trips():
    rid = new_recording_id()
    sfid = source_file_id_for(rid)
    assert sfid.startswith("chrome-extension:")
    assert is_upload_source(sfid)
    assert recording_id_from_source(sfid) == rid


def test_is_upload_source_rejects_drive_ids_and_none():
    assert is_upload_source(None) is False
    assert is_upload_source("") is False
    assert is_upload_source("1A2b3C_driveFileId") is False


def test_metadata_round_trips(tmp_path):
    rid = new_recording_id()
    meta = RecordingMetadata(
        recording_id=rid, filename=f"{rid}.webm", source="chrome-extension",
        meeting_url="https://meet.google.com/abc", meeting_title="Weekly",
        started_at="2026-06-05T10:00:00Z", ended_at="2026-06-05T10:30:00Z",
        duration_seconds=1800.0, content_type="audio/webm",
    )
    write_metadata(tmp_path, meta)
    loaded = read_metadata(tmp_path, rid)
    assert loaded == meta


def test_read_metadata_tolerates_unknown_keys(tmp_path):
    rid = new_recording_id()
    (tmp_path / f"{rid}.json").write_text(
        '{"recording_id": "%s", "filename": "%s.webm", "future_field": 1}' % (rid, rid),
        encoding="utf-8",
    )
    loaded = read_metadata(tmp_path, rid)
    assert loaded is not None and loaded.recording_id == rid


def test_resolve_recording_file_prefers_sidecar_then_globs(tmp_path):
    rid = new_recording_id()
    media = recording_path(tmp_path, rid, ".webm")
    media.write_bytes(b"webm")
    write_metadata(tmp_path, RecordingMetadata(recording_id=rid, filename=media.name))
    assert resolve_recording_file(tmp_path, rid) == media

    # No sidecar -> glob fallback (and never returns the .json sidecar).
    rid2 = new_recording_id()
    media2 = recording_path(tmp_path, rid2, ".ogg")
    media2.write_bytes(b"ogg")
    assert resolve_recording_file(tmp_path, rid2) == media2


def test_resolve_recording_file_missing_returns_none(tmp_path):
    assert resolve_recording_file(tmp_path, "nope") is None


def test_resolve_recording_file_ignores_traversal_in_sidecar(tmp_path):
    # A tampered sidecar must never resolve a path outside recordings_dir.
    rid = new_recording_id()
    (tmp_path / f"{rid}.json").write_text(
        json.dumps({"recording_id": rid, "filename": "../../etc/passwd"}),
        encoding="utf-8",
    )
    assert resolve_recording_file(tmp_path, rid) is None


def test_cleanup_recording_removes_media_and_sidecar(tmp_path):
    rid = new_recording_id()
    media = recording_path(tmp_path, rid, ".webm")
    media.write_bytes(b"x")
    write_metadata(tmp_path, RecordingMetadata(recording_id=rid, filename=media.name))
    cleanup_recording(tmp_path, rid)
    assert resolve_recording_file(tmp_path, rid) is None
    assert not (tmp_path / f"{rid}.json").exists()


def test_recordings_dir_from_env_default_and_override():
    assert recordings_dir_from_env({}) == Path("/app/data/recordings")
    assert recordings_dir_from_env({"EXTENSION_RECORDINGS_DIR": "/data/recs"}) == Path("/data/recs")
