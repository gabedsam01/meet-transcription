from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from tests.e2e.helpers import (
    build_app,
    deepgram_required_status,
    login,
    seed_auth_connected,
    seed_deepgram_key,
)
from tests.fakes import build_fake_repositories


def test_onboarding_shows_incomplete_checklist_for_a_fresh_user(tmp_path):
    worker = build_memory_repositories()
    app = build_app(tmp_path, worker=worker, transcription_status=deepgram_required_status())
    with TestClient(app) as client:
        login(client)
        page = client.get("/onboarding").text
    assert "Configuração guiada" in page
    assert "Conectar Google" in page  # CTA shown because Google is not connected
    assert "Configurar pasta" in page
    assert "Em configuração" in page


def test_onboarding_all_green_when_fully_configured(tmp_path):
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    app = build_app(tmp_path, auth=auth, worker=worker, transcription_status=deepgram_required_status())
    seed_auth_connected(auth)
    seed_deepgram_key(auth)
    # The new "extension-first" path needs the user to have an extension
    # token too. Mint one so the checklist is fully green.
    from app.web.extension_tokens import new_raw_token

    raw, token_hash, prefix = new_raw_token("a-long-secret-for-tests")
    auth.extension_tokens.create_for_user(
        1, name="E2E device", token_hash=token_hash, token_prefix=prefix
    )
    with TestClient(app) as client:
        login(client)
        page = client.get("/onboarding").text
    assert "Tudo pronto" in page


def test_onboarding_requires_login(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/onboarding", follow_redirects=False)
    assert response.status_code in {302, 303, 307}
    assert response.headers["location"].startswith("/login")
