from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


@dataclass(frozen=True)
class Settings:
    deepgram_api_key: str
    google_auth_mode: str
    google_service_account_file: Path
    source_drive_folder_id: str
    destination_drive_folder_id: str
    poll_interval_seconds: int
    tmp_dir: Path
    state_file: Path
    deepgram_model: str
    deepgram_language: str
    deepgram_smart_format: bool
    deepgram_punctuate: bool
    deepgram_diarize: bool
    deepgram_utterances: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = env or os.environ

        settings = cls(
            deepgram_api_key=_required(values, "DEEPGRAM_API_KEY"),
            google_auth_mode=_required(values, "GOOGLE_AUTH_MODE"),
            google_service_account_file=Path(
                _required(values, "GOOGLE_SERVICE_ACCOUNT_FILE")
            ),
            source_drive_folder_id=_required(values, "SOURCE_DRIVE_FOLDER_ID"),
            destination_drive_folder_id=_required(
                values, "DESTINATION_DRIVE_FOLDER_ID"
            ),
            poll_interval_seconds=_positive_int(values, "POLL_INTERVAL_SECONDS"),
            tmp_dir=Path(_required(values, "TMP_DIR")),
            state_file=Path(_required(values, "STATE_FILE")),
            deepgram_model=_required(values, "DEEPGRAM_MODEL"),
            deepgram_language=_required(values, "DEEPGRAM_LANGUAGE"),
            deepgram_smart_format=parse_bool(
                _required(values, "DEEPGRAM_SMART_FORMAT")
            ),
            deepgram_punctuate=parse_bool(_required(values, "DEEPGRAM_PUNCTUATE")),
            deepgram_diarize=parse_bool(_required(values, "DEEPGRAM_DIARIZE")),
            deepgram_utterances=parse_bool(_required(values, "DEEPGRAM_UTTERANCES")),
        )

        if settings.google_auth_mode != "service_account":
            raise ValueError("GOOGLE_AUTH_MODE must be 'service_account' for this MVP")

        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        return settings


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def _positive_int(env: Mapping[str, str], key: str) -> int:
    value = _required(env, key)
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return number
