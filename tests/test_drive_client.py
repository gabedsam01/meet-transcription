from app.drive_client import is_ready_video_file, sort_drive_files, to_drive_file


def test_is_ready_video_file_accepts_mp4_mime_type():
    assert is_ready_video_file({"name": "meeting", "mimeType": "video/mp4", "size": "10"}) is True


def test_is_ready_video_file_rejects_non_mp4_video_type():
    assert is_ready_video_file({"name": "meeting.mov", "mimeType": "video/quicktime", "size": "10"}) is False


def test_is_ready_video_file_accepts_mp4_extension_when_mime_type_is_generic():
    assert is_ready_video_file({"name": "meeting.mp4", "mimeType": "application/octet-stream", "size": "10"}) is True


def test_is_ready_video_file_rejects_trashed_or_zero_size_files():
    assert is_ready_video_file({"name": "meeting.mp4", "mimeType": "video/mp4", "trashed": True, "size": "10"}) is False
    assert is_ready_video_file({"name": "meeting.mp4", "mimeType": "video/mp4", "size": "0"}) is False


def test_to_drive_file_maps_google_payload():
    file = to_drive_file(
        {
            "id": "abc",
            "name": "meeting.mp4",
            "mimeType": "video/mp4",
            "size": "10",
            "createdTime": "2026-06-03T10:00:00Z",
            "modifiedTime": "2026-06-03T10:05:00Z",
        }
    )

    assert file.id == "abc"
    assert file.name == "meeting.mp4"
    assert file.size == 10


def test_sort_drive_files_orders_oldest_first():
    newer = to_drive_file({"id": "2", "name": "b.mp4", "mimeType": "video/mp4", "size": "10", "createdTime": "2026-06-03T11:00:00Z", "modifiedTime": "2026-06-03T11:00:00Z"})
    older = to_drive_file({"id": "1", "name": "a.mp4", "mimeType": "video/mp4", "size": "10", "createdTime": "2026-06-03T10:00:00Z", "modifiedTime": "2026-06-03T10:00:00Z"})

    assert [file.id for file in sort_drive_files([newer, older])] == ["1", "2"]
