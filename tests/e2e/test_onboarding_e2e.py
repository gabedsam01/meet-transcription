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
    assert "Onboarding" in page
    assert "Conectar Google" in page  # CTA shown because Google is not connected
    assert "Configurar pasta" in page
    assert "Em configuração" in page


def test_onboarding_all_green_when_fully_configured(tmp_path):
    auth = build_fake_repositories()
    worker = build_memory_repositories()
    app = build_app(tmp_path, auth=auth, worker=worker, transcription_status=deepgram_required_status())
    seed_auth_connected(auth)
    seed_deepgram_key(auth)
    with TestClient(app) as client:
        login(client)
        page = client.get("/onboarding").text
    assert "Tudo pronto" in page
    assert "Automação ativa" in page


def test_onboarding_requires_login(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/onboarding", follow_redirects=False)
    assert response.status_code in {302, 303, 307}
    assert response.headers["location"].startswith("/login")
