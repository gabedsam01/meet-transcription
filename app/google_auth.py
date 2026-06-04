from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import GoogleToken
from app.drive_client import DRIVE_SCOPES


def build_oauth_credentials(token: dict):
    """Build google.oauth2 Credentials from a stored web-OAuth token dict."""
    from google.oauth2.credentials import Credentials

    scopes = token.get("scopes") or DRIVE_SCOPES
    if isinstance(scopes, str):
        scopes = scopes.split()
    info = dict(token)
    if "access_token" in info and "token" not in info:
        info["token"] = info["access_token"]
    if info.get("expiry"):
        info["expiry"] = _google_expiry(info["expiry"])
    credentials = Credentials.from_authorized_user_info(info, scopes=scopes)
    # Some google-auth versions don't restore the access token from info; set it so the
    # credential is immediately usable instead of being forced to refresh on first call.
    if not credentials.token and info.get("token"):
        credentials.token = info["token"]
    return credentials


def credentials_from_token(token: GoogleToken):
    """Build google.oauth2 Credentials from a decrypted GoogleToken domain object."""
    return build_oauth_credentials(
        {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_uri": token.token_uri,
            "client_id": token.client_id,
            "client_secret": token.client_secret,
            "scopes": token.scopes,
            "expiry": token.expiry,
        }
    )


def _google_expiry(value: str) -> str:
    if value.endswith("Z"):
        return value.removesuffix("Z")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0).isoformat()
