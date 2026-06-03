from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProcessedState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, dict[str, Any]] = self._load()

    def is_processed(self, file_id: str) -> bool:
        entry = self.data.get(file_id, {})
        return bool(entry.get("processed_at") and entry.get("transcript_drive_file_id"))

    def mark_processed(
        self, file_id: str, name: str, transcript_drive_file_id: str
    ) -> None:
        self.data[file_id] = {
            "name": name,
            "processed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "transcript_drive_file_id": transcript_drive_file_id,
        }
        self.clear_failure(file_id, save=False)
        self.save()

    def mark_failed(self, file_id: str, name: str, error: str) -> None:
        existing = self.data.get(file_id, {})
        failure = existing.get("failure", {})
        attempts = int(failure.get("attempts", 0)) + 1
        updated = dict(existing)
        updated["name"] = name
        updated["failure"] = {
            "attempts": attempts,
            "last_failed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "error": error,
        }
        self.data[file_id] = updated
        self.save()

    def should_skip_failed(
        self, file_id: str, max_attempts: int, retry_after_seconds: int
    ) -> bool:
        failure = self.data.get(file_id, {}).get("failure")
        if not failure:
            return False
        attempts = int(failure.get("attempts", 0))
        if attempts >= max_attempts:
            return True
        last_failed_at = _parse_datetime(failure.get("last_failed_at"))
        if not last_failed_at:
            return False
        elapsed = (datetime.now(timezone.utc) - last_failed_at).total_seconds()
        return elapsed < retry_after_seconds

    def clear_failure(self, file_id: str, save: bool = True) -> None:
        entry = self.data.get(file_id)
        if not entry or "failure" not in entry:
            return
        del entry["failure"]
        if not entry.get("processed_at"):
            del self.data[file_id]
        if save:
            self.save()

    def remove(self, file_id: str) -> None:
        if file_id in self.data:
            del self.data[file_id]
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        content = self.path.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        loaded = json.loads(content)
        if not isinstance(loaded, dict):
            raise ValueError(f"State file must contain a JSON object: {self.path}")
        return loaded


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
