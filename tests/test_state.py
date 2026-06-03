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
