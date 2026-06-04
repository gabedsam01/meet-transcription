from __future__ import annotations

import re
from urllib.parse import urlparse

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def extract_google_drive_folder_id(value: str) -> str:
    """Extract a Drive folder ID from a full URL, a URL with querystring, or a raw ID."""
    if not value or not value.strip():
        raise ValueError("Drive folder value is required")
    text = value.strip()
    if "drive.google.com" in text or "/folders/" in text:
        match = _FOLDER_RE.search(urlparse(text).path)
        if not match:
            raise ValueError(f"Could not extract a Drive folder ID from URL: {value!r}")
        candidate = match.group(1)
    else:
        candidate = text
    if not _ID_RE.match(candidate):
        raise ValueError(f"Not a valid Drive folder ID: {candidate!r}")
    return candidate
