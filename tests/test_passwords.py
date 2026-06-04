from app.web.passwords import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_handles_missing_or_garbage_hash():
    assert verify_password("x", None) is False
    assert verify_password("x", "") is False
    assert verify_password("x", "not-a-bcrypt-hash") is False
