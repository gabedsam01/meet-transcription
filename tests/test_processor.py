from __future__ import annotations

from app.processor import DriveFile, FileProcessor, format_transcript, sanitize_filename


def test_sanitize_filename_keeps_safe_name_without_mp4_extension():
    assert sanitize_filename("wrb-gcfd-bzv 2026/06/03.mp4") == "wrb-gcfd-bzv_2026_06_03"


def test_sanitize_filename_falls_back_for_empty_name():
    assert sanitize_filename("////.mp4") == "transcricao"


def test_format_transcript_prefers_utterances():
    response = {
        "results": {
            "utterances": [
                {"start": 1.2, "speaker": 0, "transcript": "Olá mundo."}
            ]
        }
    }

    text = format_transcript(response, "video.mp4", "file123")

    assert "TRANSCRIÇÃO DA REUNIÃO" in text
    assert "Arquivo original: video.mp4" in text
    assert "ID Google Drive: file123" in text
    assert "[00:00:01] Speaker 0:" in text
    assert "Olá mundo." in text


def test_format_transcript_falls_back_to_plain_transcript():
    response = {
        "results": {
            "channels": [{"alternatives": [{"transcript": "Texto corrido."}]}]
        }
    }

    text = format_transcript(response, "video.mp4", "file123")

    assert "Texto corrido." in text


def test_processor_skips_processed_files(tmp_path):
    drive = FakeDrive([_drive_file("done", "done.mp4")])
    state = FakeState(processed={"done"})
    processor = FileProcessor(drive, FakeDeepgram(), state, tmp_path)

    assert processor.process_pending() == 0
    assert drive.downloads == []


def test_processor_marks_processed_after_upload_and_cleans_tmp(tmp_path):
    source = _drive_file("new", "meet.mp4")
    drive = FakeDrive([source])
    state = FakeState()
    processor = FileProcessor(drive, FakeDeepgram(), state, tmp_path)

    assert processor.process_pending() == 1

    assert state.marked[0] == ("new", "meet.mp4", "txt123")
    assert drive.uploaded_names == ["meet_Transcricao.txt"]
    assert list(tmp_path.iterdir()) == []


def test_processor_does_not_mark_processed_when_upload_fails(tmp_path):
    source = _drive_file("new", "meet.mp4")
    drive = FakeDrive([source], fail_upload=True)
    state = FakeState()
    processor = FileProcessor(drive, FakeDeepgram(), state, tmp_path)

    assert processor.process_pending() == 0

    assert state.marked == []
    assert list(tmp_path.iterdir()) == []


def test_processor_reprocesses_requested_processed_file(tmp_path):
    source = _drive_file("done", "done.mp4")
    drive = FakeDrive([source])
    state = FakeState(processed={"done"})
    processor = FileProcessor(drive, FakeDeepgram(), state, tmp_path)

    assert processor.process_pending(reprocess_file_id="done") == 1

    assert state.removed == []
    assert state.marked[0] == ("done", "done.mp4", "txt123")


def _drive_file(file_id: str, name: str) -> DriveFile:
    return DriveFile(
        id=file_id,
        name=name,
        mime_type="video/mp4",
        size=10,
        created_time="2026-06-03T10:00:00Z",
        modified_time="2026-06-03T10:00:00Z",
    )


class FakeDrive:
    def __init__(self, files: list[DriveFile], fail_upload: bool = False):
        self.files = files
        self.fail_upload = fail_upload
        self.downloads = []
        self.uploaded_names = []

    def list_video_files(self) -> list[DriveFile]:
        return self.files

    def download_file(self, file: DriveFile, destination):
        self.downloads.append((file.id, destination.name))
        destination.write_bytes(b"mp4 bytes")

    def upload_text_file(self, source_path, filename: str) -> str:
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.uploaded_names.append(filename)
        assert source_path.read_text(encoding="utf-8")
        return "txt123"


class FakeDeepgram:
    def transcribe(self, video_path):
        assert video_path.read_bytes() == b"mp4 bytes"
        return {
            "results": {
                "utterances": [
                    {"start": 1.0, "speaker": 0, "transcript": "Texto transcrito."}
                ]
            }
        }


class FakeState:
    def __init__(self, processed=None):
        self.processed = set(processed or set())
        self.marked = []
        self.removed = []

    def is_processed(self, file_id: str) -> bool:
        return file_id in self.processed

    def mark_processed(self, file_id: str, name: str, transcript_drive_file_id: str):
        self.marked.append((file_id, name, transcript_drive_file_id))
        self.processed.add(file_id)

    def remove(self, file_id: str):
        self.removed.append(file_id)
        self.processed.discard(file_id)
