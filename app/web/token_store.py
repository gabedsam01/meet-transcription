from __future__ import annotations

from cryptography.fernet import Fernet

from app.web.repositories import GoogleToken, GoogleTokensRepository
from app.web.security import decrypt_value, encrypt_value


class TokenStore:
    """Encrypt/decrypt Google OAuth tokens around a GoogleTokensRepository.

    Encryption stays here; the repository only persists already-encrypted text.
    ``access_token``, ``refresh_token`` and ``client_secret`` are encrypted;
    ``token_uri``, ``client_id``, ``scopes`` and ``expiry`` cross as-is.
    """

    def __init__(self, repository: GoogleTokensRepository, fernet: Fernet) -> None:
        self._repo = repository
        self._fernet = fernet

    def save_for_user(self, user_id: int, token_data: dict) -> None:
        token = GoogleToken(
            access_token=encrypt_value(self._fernet, token_data["access_token"]),
            refresh_token=encrypt_value(self._fernet, token_data.get("refresh_token")),
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=encrypt_value(self._fernet, token_data.get("client_secret")),
            scopes=token_data["scopes"],
            expiry=token_data.get("expiry"),
        )
        self._repo.save_for_user(user_id, token)

    def get_for_user(self, user_id: int) -> dict | None:
        token = self._repo.get_for_user(user_id)
        if token is None:
            return None
        return {
            "access_token": decrypt_value(self._fernet, token.access_token),
            "refresh_token": decrypt_value(self._fernet, token.refresh_token),
            "token_uri": token.token_uri,
            "client_id": token.client_id,
            "client_secret": decrypt_value(self._fernet, token.client_secret),
            "scopes": token.scopes,
            "expiry": token.expiry,
        }
