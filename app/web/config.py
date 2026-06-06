from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import parse_bool
from app.recordings import DEFAULT_RECORDINGS_DIR


@dataclass(frozen=True)
class WebSettings:
    admin_username: str
    admin_password: str
    app_secret_key: str
    session_cookie_secure: bool
    google_web_client_id: str
    google_web_client_secret: str
    google_redirect_uri: str
    database_url: str
    tmp_dir: Path
    # Chrome-extension recording upload (POST /api/recordings/upload). The feature
    # is DISABLED unless extension_upload_token is set; the token authenticates the
    # extension (Bearer). extension_upload_user_email picks the owning account
    # (defaults to the admin). Recordings land in recordings_dir, shared with the
    # worker via the ./data volume.
    extension_upload_token: str
    extension_upload_max_mb: int
    extension_upload_user_email: str
    recordings_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WebSettings":
        values = env or os.environ
        settings = cls(
            admin_username=_required(values, "ADMIN_USERNAME"),
            admin_password=_required(values, "ADMIN_PASSWORD"),
            app_secret_key=_required(values, "APP_SECRET_KEY"),
            session_cookie_secure=parse_bool(values.get("SESSION_COOKIE_SECURE", "false")),
            google_web_client_id=_required(values, "GOOGLE_WEB_CLIENT_ID"),
            google_web_client_secret=_required(values, "GOOGLE_WEB_CLIENT_SECRET"),
            google_redirect_uri=_required(values, "GOOGLE_REDIRECT_URI"),
            database_url=_required(values, "DATABASE_URL"),
            tmp_dir=Path(values.get("TMP_DIR", "/app/tmp")),
            extension_upload_token=values.get("EXTENSION_UPLOAD_TOKEN", "").strip(),
            extension_upload_max_mb=_positive_int(values, "EXTENSION_UPLOAD_MAX_MB", 500),
            extension_upload_user_email=values.get(
                "EXTENSION_UPLOAD_USER_EMAIL", ""
            ).strip(),
            recordings_dir=Path(
                values.get("EXTENSION_RECORDINGS_DIR", "").strip()
                or DEFAULT_RECORDINGS_DIR
            ),
        )
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        return settings


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default
