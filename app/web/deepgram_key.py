from __future__ import annotations

import logging
from typing import Literal

import requests

from app.web.repositories import DeepgramCredentialsRepository
from app.web.security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

DEEPGRAM_PROJECTS_URL = "https://api.deepgram.com/v1/projects"


class DeepgramKeyStore:
    def __init__(self, repository: DeepgramCredentialsRepository, fernet) -> None:
        self._repo = repository
        self._fernet = fernet

    def save_for_user(self, user_id: int, api_key: str) -> None:
        self._repo.save_for_user(user_id, encrypt_value(self._fernet, api_key))

    def get_key(self, user_id: int) -> str | None:
        encrypted = self._repo.get_encrypted_for_user(user_id)
        return decrypt_value(self._fernet, encrypted) if encrypted else None

    def has_key(self, user_id: int) -> bool:
        return self._repo.get_encrypted_for_user(user_id) is not None

    def masked(self, user_id: int) -> str | None:
        key = self.get_key(user_id)
        if not key:
            return None
        return f"…{key[-4:]}" if len(key) >= 4 else "…"


def verify_deepgram_key(
    api_key: str, *, session=None, timeout: int = 5
) -> Literal["valid", "invalid", "unverifiable"]:
    """Best-effort live check. Never raises; never logs the key."""
    http = session or requests
    try:
        response = http.get(
            DEEPGRAM_PROJECTS_URL,
            headers={"Authorization": f"Token {api_key}"},
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - network/timeout must degrade to "unverifiable"
        logger.warning("Deepgram key verification could not reach the API")
        return "unverifiable"
    if response.status_code == 200:
        return "valid"
    if response.status_code in (401, 403):
        return "invalid"
    logger.warning("Deepgram key verification got unexpected status %s", response.status_code)
    return "unverifiable"
