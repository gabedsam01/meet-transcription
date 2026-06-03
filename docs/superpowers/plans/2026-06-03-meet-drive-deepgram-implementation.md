# Meet Drive Deepgram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved MVP polling worker that transcribes shared Google Drive MP4 files with Deepgram and uploads TXT transcripts back to Drive.

**Architecture:** A single Python package under `app/` exposes a CLI worker with `--once`, `--watch`, and `--reprocess`. External integrations are isolated in Drive and Deepgram clients, while `processor.py` coordinates flow and keeps JSON state persistent and safe.

**Tech Stack:** Python 3.11, Google Drive API client, `requests`, Docker, Docker Compose, pytest for local unit tests.

---

## File Map

- Create `app/__init__.py`: package marker.
- Create `app/config.py`: environment parsing and settings validation.
- Create `app/logger.py`: console logging setup.
- Create `app/state.py`: persistent JSON state with atomic writes.
- Create `app/deepgram_client.py`: HTTP upload of MP4 bytes to Deepgram.
- Create `app/drive_client.py`: Service Account authentication, Drive listing, download, upload.
- Create `app/processor.py`: orchestration, sanitization, transcript formatting, temp cleanup.
- Create `app/main.py`: CLI entrypoint and watch loop.
- Create `tests/test_config.py`: config parsing tests.
- Create `tests/test_state.py`: processed state persistence tests.
- Create `tests/test_processor.py`: filename, transcript formatting, skip/reprocess, cleanup behavior tests.
- Create `requirements.txt`: runtime and test dependencies.
- Create `Dockerfile`: Python 3.11 container.
- Create `docker-compose.yml`: single service with persistent volumes.
- Create `.env.example`: required variables.
- Create `.gitignore`: excludes sensitive/runtime files while keeping `.gitkeep` files.
- Create `data/.gitkeep`, `tmp/.gitkeep`, `secrets/.gitkeep`: tracked empty directories.
- Create `README.md`: setup and VPS operating guide.

### Task 1: Config And Test Harness

**Files:**
- Create: `tests/test_config.py`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `requirements.txt`

- [ ] **Step 1: Write failing config tests**

```python
import pytest

from app.config import Settings, parse_bool


def test_parse_bool_accepts_expected_values():
    assert parse_bool("true") is True
    assert parse_bool("1") is True
    assert parse_bool("yes") is True
    assert parse_bool("false") is False
    assert parse_bool("0") is False
    assert parse_bool("no") is False


def test_parse_bool_rejects_invalid_value():
    with pytest.raises(ValueError, match="Invalid boolean"):
        parse_bool("maybe")


def test_settings_from_env_parses_required_values(tmp_path):
    env = {
        "DEEPGRAM_API_KEY": "dg-key",
        "GOOGLE_AUTH_MODE": "service_account",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "/app/secrets/service-account.json",
        "SOURCE_DRIVE_FOLDER_ID": "source",
        "DESTINATION_DRIVE_FOLDER_ID": "destination",
        "POLL_INTERVAL_SECONDS": "300",
        "TMP_DIR": str(tmp_path / "tmp"),
        "STATE_FILE": str(tmp_path / "data" / "processed_files.json"),
        "DEEPGRAM_MODEL": "nova-3",
        "DEEPGRAM_LANGUAGE": "pt-BR",
        "DEEPGRAM_SMART_FORMAT": "true",
        "DEEPGRAM_PUNCTUATE": "true",
        "DEEPGRAM_DIARIZE": "true",
        "DEEPGRAM_UTTERANCES": "true",
    }

    settings = Settings.from_env(env)

    assert settings.deepgram_api_key == "dg-key"
    assert settings.poll_interval_seconds == 300
    assert settings.deepgram_smart_format is True
```

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL because `app.config` does not exist.

- [ ] **Step 3: Implement config**

Create `Settings`, `parse_bool`, and required environment validation in `app/config.py`.

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

### Task 2: State Persistence

**Files:**
- Create: `tests/test_state.py`
- Create: `app/state.py`

- [ ] **Step 1: Write failing state tests**

```python
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
```

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_state.py -v`
Expected: FAIL because `app.state` does not exist.

- [ ] **Step 3: Implement state**

Create a `ProcessedState` class that loads missing files as `{}`, writes JSON through a temporary file, supports `is_processed`, `mark_processed`, and `remove`.

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_state.py -v`
Expected: PASS.

### Task 3: Processor Helpers And Orchestration

**Files:**
- Create: `tests/test_processor.py`
- Create: `app/processor.py`

- [ ] **Step 1: Write failing processor tests**

```python
from pathlib import Path

from app.processor import FileProcessor, DriveFile, format_transcript, sanitize_filename


def test_sanitize_filename_keeps_safe_name_without_mp4_extension():
    assert sanitize_filename("wrb-gcfd-bzv 2026/06/03.mp4") == "wrb-gcfd-bzv_2026_06_03"


def test_format_transcript_prefers_utterances():
    response = {"results": {"utterances": [{"start": 1.2, "speaker": 0, "transcript": "Olá mundo."}]}}

    text = format_transcript(response, "video.mp4", "file123")

    assert "TRANSCRIÇÃO DA REUNIÃO" in text
    assert "Arquivo original: video.mp4" in text
    assert "ID Google Drive: file123" in text
    assert "[00:00:01] Speaker 0:" in text
    assert "Olá mundo." in text


def test_format_transcript_falls_back_to_plain_transcript():
    response = {"results": {"channels": [{"alternatives": [{"transcript": "Texto corrido."}]}]}}

    text = format_transcript(response, "video.mp4", "file123")

    assert "Texto corrido." in text


def test_processor_skips_processed_files(tmp_path):
    drive = FakeDrive([DriveFile(id="done", name="done.mp4", mime_type="video/mp4", size=10, created_time="2026-06-03T10:00:00Z", modified_time="2026-06-03T10:00:00Z")])
    state = FakeState(processed={"done"})
    processor = FileProcessor(drive, FakeDeepgram(), state, tmp_path)

    assert processor.process_pending() == 0
    assert drive.downloads == []


def test_processor_marks_processed_after_upload_and_cleans_tmp(tmp_path):
    source = DriveFile(id="new", name="meet.mp4", mime_type="video/mp4", size=10, created_time="2026-06-03T10:00:00Z", modified_time="2026-06-03T10:00:00Z")
    drive = FakeDrive([source])
    state = FakeState()
    processor = FileProcessor(drive, FakeDeepgram(), state, tmp_path)

    assert processor.process_pending() == 1
    assert state.marked[0][0] == "new"
    assert list(tmp_path.iterdir()) == []
```

Include small fake classes in the same test file for Drive, Deepgram, and State.

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_processor.py -v`
Expected: FAIL because `app.processor` does not exist.

- [ ] **Step 3: Implement processor**

Create `DriveFile`, `sanitize_filename`, `format_transcript`, and `FileProcessor` with cleanup in `finally` and state update only after upload succeeds.

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_processor.py -v`
Expected: PASS.

### Task 4: Integrations And CLI

**Files:**
- Create: `app/logger.py`
- Create: `app/deepgram_client.py`
- Create: `app/drive_client.py`
- Create: `app/main.py`

- [ ] **Step 1: Write integration code after helper tests are green**

Implement Drive Service Account client, Deepgram HTTP client, logger setup, and CLI wiring. These depend on external services and will be smoke-tested locally by imports/compile.

- [ ] **Step 2: Run smoke checks**

Run: `python -m compileall app`
Expected: PASS.

### Task 5: Docker, Docs, And Security Files

**Files:**
- Create: `.env.example`
- Create: `.gitignore`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `README.md`
- Create: `data/.gitkeep`
- Create: `tmp/.gitkeep`
- Create: `secrets/.gitkeep`

- [ ] **Step 1: Create runtime files**

Add the exact environment variables, Compose service, Dockerfile, tracked `.gitkeep` files, and README setup steps.

- [ ] **Step 2: Run project validations**

Run: `python -m compileall app`, `python -m pytest -v`, `docker compose config`, `git status`, and `docker compose build` if Docker is available.
Expected: compile/tests/Compose config pass, Git status shows only intended project files, Docker build passes when Docker daemon is available.
