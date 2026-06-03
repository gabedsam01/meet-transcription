from __future__ import annotations

from cryptography.fernet import Fernet

from app import db
from app.web.security import decrypt_value, encrypt_value


class TokenStore:
    def __init__(self, db_path, fernet: Fernet):
        self.db_path = db_path
        self.fernet = fernet

    def save_for_user(self, user_id: int, token_data: dict) -> None:
        encrypted = dict(token_data)
        encrypted["access_token"] = encrypt_value(self.fernet, token_data["access_token"])
        encrypted["refresh_token"] = encrypt_value(
            self.fernet, token_data.get("refresh_token")
        )
        encrypted["client_secret"] = encrypt_value(
            self.fernet, token_data.get("client_secret")
        )
        db.save_google_token(self.db_path, user_id, encrypted)

    def get_for_user(self, user_id: int) -> dict | None:
        row = db.get_google_token(self.db_path, user_id)
        if row is None:
            return None
        return {
            "access_token": decrypt_value(self.fernet, row["access_token"]),
            "refresh_token": decrypt_value(self.fernet, row["refresh_token"]),
            "token_uri": row["token_uri"],
            "client_id": row["client_id"],
            "client_secret": decrypt_value(self.fernet, row["client_secret"]),
            "scopes": row["scopes"],
            "expiry": row["expiry"],
        }
