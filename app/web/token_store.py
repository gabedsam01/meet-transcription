from __future__ import annotations

from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.database.repositories import GoogleTokenRepository
from app.web.security import decrypt_value, encrypt_value


class TokenStore:
    """Encrypt/decrypt Google OAuth tokens around the google_tokens table.

    Encryption stays here; the repository only persists already-encrypted text.
    ``access_token``, ``refresh_token`` and ``client_secret`` are encrypted;
    ``token_uri``, ``client_id``, ``scopes`` and ``expiry`` are stored as-is.
    """

    def __init__(self, fernet: Fernet):
        self.fernet = fernet

    def save_for_user(self, session: Session, user_id: int, token_data: dict) -> None:
        scopes = token_data.get("scopes")
        if isinstance(scopes, str):
            scopes = scopes.split()
        GoogleTokenRepository(session).upsert_for_user(
            user_id,
            encrypted_access_token=encrypt_value(self.fernet, token_data["access_token"]),
            encrypted_refresh_token=encrypt_value(
                self.fernet, token_data.get("refresh_token")
            ),
            token_uri=token_data["token_uri"],
            client_id=token_data.get("client_id"),
            client_secret=encrypt_value(self.fernet, token_data.get("client_secret")),
            scopes=scopes,
            expiry=_parse_expiry(token_data.get("expiry")),
        )

    def get_for_user(self, session: Session, user_id: int) -> dict | None:
        token = GoogleTokenRepository(session).get_for_user(user_id)
        if token is None:
            return None
        return {
            "access_token": decrypt_value(self.fernet, token.encrypted_access_token),
            "refresh_token": decrypt_value(self.fernet, token.encrypted_refresh_token),
            "token_uri": token.token_uri,
            "client_id": token.client_id,
            "client_secret": decrypt_value(self.fernet, token.client_secret),
            "scopes": token.scopes,
            "expiry": token.expiry.isoformat() if token.expiry else None,
        }


def _parse_expiry(value) -> datetime | None:
    """Coerce an OAuth ``expiry`` (ISO string or datetime) into a tz-aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
