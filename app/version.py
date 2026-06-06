"""Build/version metadata surfaced by ``GET /version``.

The values come from environment variables that the deploy pipeline (Docker
build args → compose env) can inject; each has a safe, secret-free default so the
endpoint always answers. Nothing here is sensitive.
"""

from __future__ import annotations

import os
from typing import Mapping

#: Bumped by hand on release; ``APP_VERSION`` overrides it in a built image.
APP_VERSION = "0.2.0"
APP_NAME = "Meet Transcription"


def get_version_info(env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Return ``{app, version, commit, build_time}`` (never raises, no secrets)."""
    values = env if env is not None else os.environ
    return {
        "app": APP_NAME,
        "version": (values.get("APP_VERSION", "").strip() or APP_VERSION),
        "commit": (
            values.get("GIT_COMMIT", "").strip()
            or values.get("GIT_SHA", "").strip()
            or "unknown"
        ),
        "build_time": (values.get("BUILD_TIME", "").strip() or None),
    }


__all__ = ["APP_VERSION", "APP_NAME", "get_version_info"]
