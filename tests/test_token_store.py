from app.web.repositories import GoogleToken
from app.web.security import decrypt_value, encrypt_value, fernet_from_secret
from app.web.token_store import TokenStore
from tests.fakes import InMemoryGoogleTokensRepository


def test_encrypt_value_does_not_store_plaintext():
    fernet = fernet_from_secret("a-long-secret-for-tests")
    encrypted = encrypt_value(fernet, "secret-token")
    assert encrypted != "secret-token"
    assert decrypt_value(fernet, encrypted) == "secret-token"


def test_token_store_encrypts_at_rest_and_decrypts_on_read():
    repo = InMemoryGoogleTokensRepository()
    fernet = fernet_from_secret("a-long-secret-for-tests")
    store = TokenStore(repo, fernet)

    store.save_for_user(1, {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": "https://www.googleapis.com/auth/drive",
        "expiry": "2026-06-03T10:00:00Z",
    })

    stored = repo.get_for_user(1)
    assert isinstance(stored, GoogleToken)
    assert stored.access_token != "access-secret"
    assert stored.client_secret != "client-secret"

    loaded = store.get_for_user(1)
    assert loaded["access_token"] == "access-secret"
    assert loaded["refresh_token"] == "refresh-secret"
    assert loaded["client_secret"] == "client-secret"
    assert store.get_for_user(2) is None
