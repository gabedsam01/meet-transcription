from app.state import ProcessedState


def test_state_starts_empty_when_file_missing(tmp_path):
    state = ProcessedState(tmp_path / "processed_files.json")
    assert state.is_processed("abc") is False


def test_state_marks_file_processed_and_persists(tmp_path):
    path = tmp_path / "processed_files.json"
    state = ProcessedState(path)
    state.mark_processed("abc", "meeting.mp4", "txt123")

    reloaded = ProcessedState(path)

    assert reloaded.is_processed("abc") is True
    assert reloaded.data["abc"]["name"] == "meeting.mp4"
    assert reloaded.data["abc"]["transcript_drive_file_id"] == "txt123"


def test_state_records_failure_attempts_and_skips_until_limit(tmp_path):
    path = tmp_path / "processed_files.json"
    state = ProcessedState(path)

    state.mark_failed("abc", "meeting.mp4", "upload failed")
    state.mark_failed("abc", "meeting.mp4", "upload failed again")

    reloaded = ProcessedState(path)

    assert reloaded.data["abc"]["failure"]["attempts"] == 2
    assert reloaded.is_processed("abc") is False
    assert reloaded.should_skip_failed("abc", max_attempts=2, retry_after_seconds=86400) is True


def test_state_clears_failure_when_file_is_processed(tmp_path):
    path = tmp_path / "processed_files.json"
    state = ProcessedState(path)
    state.mark_failed("abc", "meeting.mp4", "upload failed")

    state.mark_processed("abc", "meeting.mp4", "txt123")

    reloaded = ProcessedState(path)

    assert "failure" not in reloaded.data["abc"]
    assert reloaded.is_processed("abc") is True


def test_state_preserves_processed_metadata_when_reprocess_failure_is_recorded(tmp_path):
    path = tmp_path / "processed_files.json"
    state = ProcessedState(path)
    state.mark_processed("abc", "meeting.mp4", "txt123")

    state.mark_failed("abc", "meeting.mp4", "upload failed")

    reloaded = ProcessedState(path)

    assert reloaded.is_processed("abc") is True
    assert reloaded.data["abc"]["transcript_drive_file_id"] == "txt123"
    assert reloaded.data["abc"]["failure"]["attempts"] == 1
