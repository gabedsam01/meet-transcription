from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import parse_bool


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
        )
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        return settings


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value
