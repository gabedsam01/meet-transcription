from app.web.provider_keys import ProviderKeyStore, verify_provider_key
from app.web.security import fernet_from_secret
from tests.fakes import InMemoryProviderCredentialsRepository


def _store():
    return ProviderKeyStore(InMemoryProviderCredentialsRepository(), fernet_from_secret("s"))


def test_save_encrypts_and_masks_only_tail():
    store = _store()
    store.save(1, "openrouter", "sk-supersecret")
    assert store.get(1, "openrouter") == "sk-supersecret"
    assert store.has(1, "openrouter") is True
    masked = store.masked(1, "openrouter")
    assert masked == "…cret"
    assert "supersecret" not in masked


def test_ciphertext_at_rest_differs_from_plaintext():
    repo = InMemoryProviderCredentialsRepository()
    store = ProviderKeyStore(repo, fernet_from_secret("s"))
    store.save(7, "gemini", "plain-key")
    assert repo.get_encrypted(7, "gemini") != "plain-key"


def test_configured_providers_lists_saved():
    store = _store()
    store.save(1, "deepgram", "k1")
    store.save(1, "gemini", "k2")
    assert store.configured_providers(1) == {"deepgram", "gemini"}


def test_has_false_when_absent():
    store = _store()
    assert store.has(1, "openrouter") is False
    assert store.masked(1, "openrouter") is None


def test_legacy_deepgram_credential_is_visible_through_store():
    # "Deepgram antigo é compatível": a key saved in the legacy table is read via
    # the new per-provider store (the fake mirrors the Postgres adapter fallback).
    from app.web.security import encrypt_value
    from tests.fakes import InMemoryDeepgramCredentialsRepository

    fernet = fernet_from_secret("s")
    legacy = InMemoryDeepgramCredentialsRepository()
    legacy.save_for_user(1, encrypt_value(fernet, "old-dg-key"))
    store = ProviderKeyStore(
        InMemoryProviderCredentialsRepository(legacy_deepgram=legacy), fernet
    )
    assert store.has(1, "deepgram") is True
    assert store.get(1, "deepgram") == "old-dg-key"
    assert store.masked(1, "deepgram") == "…-key"
    assert "deepgram" in store.configured_providers(1)
    # A new save shadows the legacy value.
    store.save(1, "deepgram", "new-dg-key")
    assert store.get(1, "deepgram") == "new-dg-key"


# --- verify_provider_key -----------------------------------------------------


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _Session:
    def __init__(self, status_code=200, raises=None):
        self._status = status_code
        self._raises = raises
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._raises:
            raise self._raises
        return _Resp(self._status)


def test_verify_openrouter_uses_bearer():
    session = _Session(200)
    assert verify_provider_key("openrouter", "k", session=session) == "valid"
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer k"


def test_verify_openrouter_invalid():
    assert verify_provider_key("openrouter", "k", session=_Session(401)) == "invalid"


def test_verify_gemini_uses_query_key_and_400_is_invalid():
    session = _Session(200)
    assert verify_provider_key("gemini", "k", session=session) == "valid"
    assert session.calls[0][1]["params"]["key"] == "k"
    assert verify_provider_key("gemini", "k", session=_Session(400)) == "invalid"


def test_verify_network_error_is_unverifiable():
    assert verify_provider_key(
        "openrouter", "k", session=_Session(raises=ConnectionError("x"))
    ) == "unverifiable"


def test_verify_empty_key_is_invalid():
    assert verify_provider_key("gemini", "  ") == "invalid"


def test_verify_unknown_provider_is_unverifiable():
    assert verify_provider_key("mystery", "k") == "unverifiable"
