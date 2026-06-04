from __future__ import annotations

import re
from datetime import datetime

# A Google Drive id is the URL-safe token Drive uses for files and folders.
_DRIVE_FOLDER_URL = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_DRIVE_FILE_URL = re.compile(r"/d/([A-Za-z0-9_-]+)")
_DRIVE_ID_PARAM = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")


def extract_drive_folder_id(value: str | None) -> str:
    """Accept a Google Drive folder URL or a bare id and return the id.

    People paste folder links straight from the Drive address bar, e.g.
    ``https://drive.google.com/drive/folders/1zv32Q...tBD5?usp=sharing``.
    This pulls the id out so the rest of the app keeps storing bare ids.
    A value that is already a bare id is returned unchanged; whitespace is
    trimmed. Returns "" for empty input.
    """
    text = (value or "").strip()
    if not text:
        return ""
    for pattern in (_DRIVE_FOLDER_URL, _DRIVE_FILE_URL, _DRIVE_ID_PARAM):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return text


def middle_truncate(value, head: int = 6, tail: int = 4) -> str:
    """Shorten a long id to ``head…tail`` so it never blows out a table cell.

    Short values are returned untouched. None renders as an em dash.
    """
    if value is None:
        return "—"
    text = str(value)
    if len(text) <= head + tail + 1:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def short_datetime(value) -> str:
    """Render an ISO timestamp as ``YYYY-MM-DD HH:MM`` (no seconds/zone).

    Falls back to the raw string if it is empty or not parseable, so the UI
    never crashes on an unexpected format.
    """
    if not value:
        return "—"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M")


def drive_download_url(file_id) -> str:
    """Build a direct-download Drive URL for a transcript file id."""
    return f"https://drive.google.com/uc?export=download&id={file_id}"
