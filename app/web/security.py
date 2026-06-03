from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def fernet_from_secret(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_value(fernet: Fernet, value: str | None) -> str | None:
    if value is None:
        return None
    return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(fernet: Fernet, value: str | None) -> str | None:
    if value is None:
        return None
    return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
