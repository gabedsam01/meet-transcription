from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from tests.e2e.helpers import build_app, login


def test_browser_404_renders_friendly_html_without_traceback(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        response = client.get("/does-not-exist", headers={"accept": "text/html"})
    assert response.status_code == 404
    body = response.text
    assert "Algo deu errado" in body
    assert "Voltar ao início" in body
    assert "Traceback" not in body


def test_api_404_stays_json(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        response = client.get("/does-not-exist")  # default accept */*
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_job_detail_404_keeps_message_in_friendly_panel(tmp_path):
    worker = build_memory_repositories()
    app = build_app(tmp_path, worker=worker)
    with TestClient(app) as client:
        login(client)
        response = client.get("/jobs/999")
    assert response.status_code == 404
    assert "Job not found" in response.text
    assert "Traceback" not in response.text


def test_download_of_missing_job_is_safe(tmp_path):
    worker = build_memory_repositories()
    app = build_app(tmp_path, worker=worker)
    with TestClient(app) as client:
        login(client)
        response = client.get("/jobs/999/download")
    assert response.status_code == 404
    assert "Traceback" not in response.text
