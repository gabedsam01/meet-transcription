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
        return file_id in self.data

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
