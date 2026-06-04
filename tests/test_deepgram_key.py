from app.web.deepgram_key import DeepgramKeyStore, verify_deepgram_key
from app.web.security import fernet_from_secret
from tests.fakes import InMemoryDeepgramCredentialsRepository


def _store():
    return DeepgramKeyStore(
        InMemoryDeepgramCredentialsRepository(), fernet_from_secret("a-long-secret-for-tests")
    )


def test_store_encrypts_and_roundtrips_and_masks():
    store = _store()
    assert store.has_key(1) is False
    store.save_for_user(1, "dg-supersecretkey")
    assert store.has_key(1) is True
    # stored value is ciphertext, not the plaintext key
    assert store._repo.get_encrypted_for_user(1) != "dg-supersecretkey"
    assert store.get_key(1) == "dg-supersecretkey"
    assert store.masked(1).endswith("tkey")
    assert "dg-supersecretkey" not in store.masked(1)


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _Session:
    def __init__(self, status=None, exc=None):
        self._status = status
        self._exc = exc
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        if self._exc:
            raise self._exc
        return _Resp(self._status)


def test_verify_returns_valid_invalid_unverifiable():
    assert verify_deepgram_key("k", session=_Session(status=200)) == "valid"
    assert verify_deepgram_key("k", session=_Session(status=401)) == "invalid"
    assert verify_deepgram_key("k", session=_Session(status=403)) == "invalid"
    assert verify_deepgram_key("k", session=_Session(status=500)) == "unverifiable"
    assert verify_deepgram_key("k", session=_Session(exc=TimeoutError())) == "unverifiable"


def test_verify_sends_token_header_not_logged():
    session = _Session(status=200)
    verify_deepgram_key("secret-key", session=session)
    _, headers, _ = session.calls[0]
    assert headers["Authorization"] == "Token secret-key"
