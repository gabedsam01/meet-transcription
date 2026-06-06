from fastapi.testclient import TestClient

from app.web.config import WebSettings
from app.web.main import create_app
from tests.fakes import build_fake_repositories


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env({
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
    })


def _client(tmp_path):
    repos = build_fake_repositories()
    return TestClient(create_app(_settings(tmp_path), repositories=repos)), repos


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "secret"},
                    follow_redirects=False)
    assert r.status_code in {302, 303}
    return r


def _admin_id(repos):
    return repos.users.get_by_email("admin").id


def test_models_page_renders(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        page = client.get("/models")
    assert page.status_code == 200
    assert "Deepgram" in page.text and "OpenRouter" in page.text and "Gemini" in page.text


def test_save_primary_provider_and_model(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client)
        client.post("/models/provider", data={
            "provider": "openrouter", "model": "openai/whisper-large-v3",
        }, follow_redirects=False)
    saved = repos.model_settings.get_for_user(_admin_id(repos))
    assert saved.primary_provider == "openrouter"
    assert saved.primary_model == "openai/whisper-large-v3"


def test_invalid_model_is_clamped_on_save(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client)
        client.post("/models/provider", data={"provider": "gemini", "model": "nope"},
                    follow_redirects=False)
    saved = repos.model_settings.get_for_user(_admin_id(repos))
    assert saved.primary_provider == "gemini"
    assert saved.primary_model == "gemini-2.5-flash"  # default for gemini


def test_save_credentials_encrypted_and_masked_in_ui(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client)
        client.post("/models/credentials", data={
            "provider": "gemini", "api_key": "gem-supersecret",
        }, follow_redirects=False)
        admin = _admin_id(repos)
        stored = repos.provider_credentials.get_encrypted(admin, "gemini")
        assert stored is not None and stored != "gem-supersecret"
        page = client.get("/models").text
    assert "gem-supersecret" not in page  # never echoed
    assert "…cret" in page  # masked tail shown


def test_save_fallback(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client)
        client.post("/models/provider", data={
            "provider": "openrouter", "model": "openai/whisper-large-v3",
        }, follow_redirects=False)
        client.post("/models/fallback", data={
            "fallback_enabled": "true",
            "fallback_provider": "deepgram",
            "fallback_model": "nova-3",
        }, follow_redirects=False)
    saved = repos.model_settings.get_for_user(_admin_id(repos))
    assert saved.fallback_enabled is True
    assert saved.fallback_provider == "deepgram"
    assert saved.fallback_model == "nova-3"


def test_test_provider_without_key_flashes_hint(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        client.post("/models/test", data={"provider": "openrouter"}, follow_redirects=False)
        page = client.get("/models").text
    assert "Configure a API key deste provedor primeiro." in page


def test_deepgram_alias_get_redirects(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        r = client.get("/settings/deepgram", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/models?provider=deepgram"


def test_deepgram_alias_post_saves_to_provider_credentials(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client)
        client.post("/settings/deepgram", data={"deepgram_api_key": "dg-key-xyz"},
                    follow_redirects=False)
    assert repos.provider_credentials.get_encrypted(_admin_id(repos), "deepgram") is not None


def test_legacy_deepgram_key_shows_configured_on_models_page(tmp_path):
    # A key stored only in the legacy deepgram_credentials table (pre-Models-tab)
    # must render as "Configurado" on the Models page (backward compatibility).
    from app.web.security import encrypt_value, fernet_from_secret

    client, repos = _client(tmp_path)
    with client:
        _login(client)
        admin = _admin_id(repos)
        fernet = fernet_from_secret("a-long-secret-for-tests")
        repos.deepgram_credentials.save_for_user(admin, encrypt_value(fernet, "legacy-dg-key"))
        page = client.get("/models").text
    assert "legacy-dg-key" not in page  # never echoed
    assert "Configurado" in page  # the legacy Deepgram key is recognised
