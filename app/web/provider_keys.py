"""Per-user, per-provider API key storage and best-effort live verification.

The store encrypts at the web boundary (Fernet, key derived from APP_SECRET_KEY)
and only ever exposes a masked tail to the UI — the full key is written once and
never rendered again. ``verify_provider_key`` does a cheap authenticated GET per
provider; it never raises and never logs the key.
"""

from __future__ import annotations

import logging
from typing import Literal

import requests

from app.web.deepgram_key import verify_deepgram_key
from app.web.repositories import ProviderCredentialsRepository
from app.web.security import decrypt_value, encrypt_value
from app.transcription.provider_models import DEEPGRAM, GEMINI, OPENROUTER, ASSEMBLYAI

logger = logging.getLogger(__name__)

VerifyResult = Literal["valid", "invalid", "unverifiable"]

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class ProviderKeyStore:
    """Encrypted per-provider key store used by the Models tab."""

    def __init__(self, repository: ProviderCredentialsRepository, fernet) -> None:
        self._repo = repository
        self._fernet = fernet

    def save(self, user_id: int, provider: str, api_key: str) -> None:
        self._repo.save(user_id, provider, encrypt_value(self._fernet, api_key))

    def get(self, user_id: int, provider: str) -> str | None:
        encrypted = self._repo.get_encrypted(user_id, provider)
        return decrypt_value(self._fernet, encrypted) if encrypted else None

    def has(self, user_id: int, provider: str) -> bool:
        return self._repo.get_encrypted(user_id, provider) is not None

    def masked(self, user_id: int, provider: str) -> str | None:
        key = self.get(user_id, provider)
        if not key:
            return None
        if provider == ASSEMBLYAI:
            import json
            try:
                data = json.loads(key)
                real_key = data.get("api_key", "")
            except Exception:
                real_key = key
            return f"…{real_key[-4:]}" if len(real_key) >= 4 else "…"
        return f"…{key[-4:]}" if len(key) >= 4 else "…"

    def configured_providers(self, user_id: int) -> set[str]:
        return set(self._repo.list_encrypted(user_id).keys())


def verify_provider_key(
    provider: str, api_key: str, *, session=None, timeout: int = 5
) -> VerifyResult:
    """Best-effort live check for a provider key. Never raises; never logs the key."""
    if not (api_key or "").strip():
        return "invalid"
    if provider == DEEPGRAM:
        return verify_deepgram_key(api_key, session=session, timeout=timeout)
    if provider == OPENROUTER:
        return _verify_bearer(OPENROUTER_KEY_URL, api_key, session=session, timeout=timeout)
    if provider == GEMINI:
        return _verify_query_key(GEMINI_MODELS_URL, api_key, session=session, timeout=timeout)
    if provider == ASSEMBLYAI:
        import json
        try:
            data = json.loads(api_key)
            real_key = data.get("api_key", "")
        except Exception:
            real_key = api_key
        return _verify_assemblyai(real_key, session=session, timeout=timeout)
    return "unverifiable"


def _verify_bearer(url: str, api_key: str, *, session, timeout: int) -> VerifyResult:
    http = session or requests
    try:
        response = http.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
    except Exception:  # noqa: BLE001 - network/timeout degrades to "unverifiable"
        logger.warning("Provider key verification could not reach %s", url)
        return "unverifiable"
    return _classify(response.status_code, url)


def _verify_query_key(url: str, api_key: str, *, session, timeout: int) -> VerifyResult:
    http = session or requests
    try:
        response = http.get(url, params={"key": api_key}, timeout=timeout)
    except Exception:  # noqa: BLE001
        logger.warning("Provider key verification could not reach %s", url)
        return "unverifiable"
    # Gemini returns 400 (not 401) for a bad API key.
    if response.status_code == 400:
        return "invalid"
    return _classify(response.status_code, url)


def _classify(status_code: int, url: str) -> VerifyResult:
    if status_code == 200:
        return "valid"
    if status_code in (401, 403):
        return "invalid"
    logger.warning("Provider key verification got unexpected status %s from %s", status_code, url)
    return "unverifiable"


def _verify_assemblyai(api_key: str, *, session=None, timeout: int) -> VerifyResult:
    http = session or requests
    try:
        response = http.get(
            "https://api.assemblyai.com/v2/transcript?limit=1",
            headers={"Authorization": api_key},
            timeout=timeout,
        )
    except Exception:
        logger.warning("AssemblyAI key verification could not reach API")
        return "unverifiable"
    return _classify(response.status_code, "https://api.assemblyai.com/v2/transcript")
