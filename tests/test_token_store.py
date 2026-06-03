from app.database.repositories import GoogleTokenRepository, UserRepository
from app.web.security import decrypt_value, encrypt_value, fernet_from_secret
from app.web.token_store import TokenStore


def test_encrypt_value_does_not_store_plaintext():
    """Pure logic: Fernet round-trip, no database."""
    fernet = fernet_from_secret("a-long-secret-for-tests")

    encrypted = encrypt_value(fernet, "secret-token")

    assert encrypted != "secret-token"
    assert decrypt_value(fernet, encrypted) == "secret-token"


def test_token_store_encrypts_and_decrypts_tokens(db):
    user = UserRepository(db).create(email="admin@example.com", name="Admin", role="admin")
    db.flush()
    fernet = fernet_from_secret("a-long-secret-for-tests")
    store = TokenStore(fernet)

    store.save_for_user(
        db,
        user.id,
        {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": "https://www.googleapis.com/auth/drive",
            "expiry": "2026-06-03T10:00:00Z",
        },
    )
    db.flush()

    raw = GoogleTokenRepository(db).get_for_user(user.id)
    assert raw.encrypted_access_token != "access-secret"
    assert raw.encrypted_refresh_token != "refresh-secret"
    assert raw.client_secret != "client-secret"  # encrypted
    assert raw.client_id == "client-id"  # not encrypted
    assert raw.scopes == ["https://www.googleapis.com/auth/drive"]  # string -> JSONB list
    assert raw.expiry is not None  # ISO string parsed into a timestamp

    loaded = store.get_for_user(db, user.id)
    assert loaded["access_token"] == "access-secret"
    assert loaded["refresh_token"] == "refresh-secret"
    assert loaded["client_secret"] == "client-secret"
    assert loaded["client_id"] == "client-id"


def test_token_store_returns_none_when_absent(db):
    user = UserRepository(db).create(email="nobody@example.com")
    db.flush()
    store = TokenStore(fernet_from_secret("a-long-secret-for-tests"))

    assert store.get_for_user(db, user.id) is None
