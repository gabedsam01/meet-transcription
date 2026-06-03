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
    google_service_account_file: Path | None
    google_oauth_client_secrets_file: Path | None
    google_oauth_token_file: Path | None
    source_drive_folder_id: str
    destination_drive_folder_id: str
    poll_interval_seconds: int
    tmp_dir: Path
    state_file: Path
    max_processing_attempts: int
    failed_retry_after_seconds: int
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
            google_service_account_file=_optional_path(
                values, "GOOGLE_SERVICE_ACCOUNT_FILE"
            ),
            google_oauth_client_secrets_file=_optional_path(
                values, "GOOGLE_OAUTH_CLIENT_SECRETS_FILE"
            ),
            google_oauth_token_file=_optional_path(
                values, "GOOGLE_OAUTH_TOKEN_FILE"
            ),
            source_drive_folder_id=_required(values, "SOURCE_DRIVE_FOLDER_ID"),
            destination_drive_folder_id=_required(
                values, "DESTINATION_DRIVE_FOLDER_ID"
            ),
            poll_interval_seconds=_positive_int(values, "POLL_INTERVAL_SECONDS"),
            tmp_dir=Path(_required(values, "TMP_DIR")),
            state_file=Path(_required(values, "STATE_FILE")),
            max_processing_attempts=_positive_int(
                values, "MAX_PROCESSING_ATTEMPTS", default="2"
            ),
            failed_retry_after_seconds=_positive_int(
                values, "FAILED_RETRY_AFTER_SECONDS", default="86400"
            ),
            deepgram_model=_required(values, "DEEPGRAM_MODEL"),
            deepgram_language=_required(values, "DEEPGRAM_LANGUAGE"),
            deepgram_smart_format=parse_bool(
                _required(values, "DEEPGRAM_SMART_FORMAT")
            ),
            deepgram_punctuate=parse_bool(_required(values, "DEEPGRAM_PUNCTUATE")),
            deepgram_diarize=parse_bool(_required(values, "DEEPGRAM_DIARIZE")),
            deepgram_utterances=parse_bool(_required(values, "DEEPGRAM_UTTERANCES")),
        )

        if settings.google_auth_mode not in {"service_account", "oauth"}:
            raise ValueError("GOOGLE_AUTH_MODE must be 'service_account' or 'oauth'")
        if settings.google_auth_mode == "service_account" and not settings.google_service_account_file:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE is required for service_account mode")
        if settings.google_auth_mode == "oauth":
            if not settings.google_oauth_client_secrets_file:
                raise ValueError("GOOGLE_OAUTH_CLIENT_SECRETS_FILE is required for oauth mode")
            if not settings.google_oauth_token_file:
                raise ValueError("GOOGLE_OAUTH_TOKEN_FILE is required for oauth mode")

        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        return settings


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def _optional_path(env: Mapping[str, str], key: str) -> Path | None:
    value = env.get(key, "").strip()
    return Path(value) if value else None


def _positive_int(env: Mapping[str, str], key: str, default: str | None = None) -> int:
    value = env.get(key, default or "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return number
