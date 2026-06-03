from app.web.security import decrypt_value, encrypt_value, fernet_from_secret
from app import db
from app.web.token_store import TokenStore


def test_encrypt_value_does_not_store_plaintext():
    fernet = fernet_from_secret("a-long-secret-for-tests")

    encrypted = encrypt_value(fernet, "secret-token")

    assert encrypted != "secret-token"
    assert decrypt_value(fernet, encrypted) == "secret-token"


def test_token_store_encrypts_and_decrypts_tokens(tmp_path):
    db_path = tmp_path / "app.db"
    db.init_db(db_path)
    user = db.get_or_create_user(db_path, "admin@example.com", "Admin")
    fernet = fernet_from_secret("a-long-secret-for-tests")
    store = TokenStore(db_path, fernet)

    store.save_for_user(
        user["id"],
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

    with db.connect_db(db_path) as conn:
        row = conn.execute("SELECT * FROM google_tokens WHERE user_id = ?", (user["id"],)).fetchone()

    assert row["access_token"] != "access-secret"
    assert row["refresh_token"] != "refresh-secret"
    assert row["client_secret"] != "client-secret"

    loaded = store.get_for_user(user["id"])
    assert loaded["access_token"] == "access-secret"
    assert loaded["refresh_token"] == "refresh-secret"
    assert loaded["client_secret"] == "client-secret"
