from app.core.models import GoogleToken
from app.google_auth import build_oauth_credentials, credentials_from_token


def test_build_oauth_credentials_maps_web_token_format():
    credentials = build_oauth_credentials(
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": "https://www.googleapis.com/auth/drive",
            "expiry": "2026-06-03T00:00:00+00:00",
        }
    )
    assert credentials.token == "access-token"
    assert credentials.refresh_token == "refresh-token"


def test_credentials_from_token_uses_domain_object():
    token = GoogleToken(
        access_token="access-token", token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id", refresh_token="refresh-token",
        client_secret="client-secret",
        scopes="https://www.googleapis.com/auth/drive",
        expiry="2026-06-03T00:00:00+00:00",
    )
    credentials = credentials_from_token(token)
    assert credentials.token == "access-token"
    assert credentials.refresh_token == "refresh-token"
