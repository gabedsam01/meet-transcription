"""Per-user Chrome extension upload tokens.

Each user may generate any number of upload tokens, each scoped to that one
user. The real token is shown to the user EXACTLY ONCE at creation; from then
on we only have a salted hash.

Hash format: ``sha256(pepper || raw_token)`` rendered as lowercase hex. The
pepper is derived from ``APP_SECRET_KEY`` so a database dump alone is not
enough to brute-force a token. The token itself is URL-safe (``secrets``
``token_urlsafe``) and prefixed with ``mtrec_`` for easy operator grepping.

Constants:
- ``TOKEN_PREFIX`` is the literal prefix the user sees/copies.
- ``RAW_TOKEN_BYTES`` is the entropy source for the random part.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# Public prefix the user copies. The full token is ``mtrec_<random>``; the
# prefix is short enough to recognize but never the only secret.
TOKEN_PREFIX = "mtrec_"
# Number of random bytes for the secret half (16 → ~22 url-safe chars).
RAW_TOKEN_BYTES = 24

# Pepper length for SHA-256: the secret stays server-side (env-derived), never
# stored in the row, so a DB leak alone cannot replay tokens.
_PEPPER_MIN = 16


def _pepper(secret_key: str) -> bytes:
    """Derive a fixed-length pepper from ``APP_SECRET_KEY`` (or any seed).

    A SHA-256 digest is plenty for a server-side pepper: the secret is the
    primary defense, the pepper just makes rainbow tables useless.
    """
    raw = (secret_key or "").encode("utf-8")
    if len(raw) < _PEPPER_MIN:
        # Never use an empty/short pepper — that would defeat the whole point.
        raise ValueError("APP_SECRET_KEY too short to derive an extension-token pepper.")
    return hashlib.sha256(b"meet-transcription/extension-token-pepper:" + raw).digest()


def new_raw_token(secret_key: str) -> tuple[str, str, str]:
    """Generate a fresh token triple.

    Returns ``(raw_token, token_hash, token_prefix)``:
    - ``raw_token``: the value the user copies now and never sees again.
    - ``token_hash``: persisted; never reversed.
    - ``token_prefix``: shown in the UI for the masked list (e.g.
      ``"mtrec_abcd…wxyz"``).
    """
    random_part = secrets.token_urlsafe(RAW_TOKEN_BYTES)
    raw = f"{TOKEN_PREFIX}{random_part}"
    digest = _hash(secret_key, raw)
    return raw, digest, _format_prefix(raw)


def hash_token(secret_key: str, raw_token: str) -> str:
    """Compute the persisted hash for a raw token (used on the auth path)."""
    return _hash(secret_key, raw_token)


def verify_token(secret_key: str, raw_token: str, stored_hash: str) -> bool:
    """Constant-time comparison of a raw token against the persisted hash."""
    if not raw_token or not stored_hash:
        return False
    candidate = _hash(secret_key, raw_token)
    return hmac.compare_digest(candidate, stored_hash)


def _hash(secret_key: str, raw_token: str) -> str:
    pepper = _pepper(secret_key)
    digest = hashlib.sha256(pepper + raw_token.encode("utf-8")).hexdigest()
    return digest


def _format_prefix(raw_token: str) -> str:
    """Short masked display, e.g. ``mtrec_abcd…wxyz`` (8 visible chars)."""
    if len(raw_token) <= 12:
        return raw_token
    return f"{raw_token[:8]}\u2026{raw_token[-4:]}"


__all__ = [
    "TOKEN_PREFIX",
    "RAW_TOKEN_BYTES",
    "new_raw_token",
    "hash_token",
    "verify_token",
]
