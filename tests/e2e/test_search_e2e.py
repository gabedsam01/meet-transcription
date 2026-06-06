from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from tests.e2e.helpers import ADMIN_ID, build_app, login, now


def _seed_transcript(worker, text, name="meet.mp4", user_id=ADMIN_ID):
    job = worker.jobs.create_job(user_id, "file", name, now())
    worker.transcripts.create(job.id, user_id, text, None, None, now())
    return job.id


def test_search_finds_user_transcript_with_snippet_and_link(tmp_path):
    worker = build_memory_repositories()
    job_id = _seed_transcript(worker, "Discutimos o orçamento e o cronograma do projeto.")
    _seed_transcript(worker, "Outra reunião sobre contratações.")
    app = build_app(tmp_path, worker=worker)
    with TestClient(app) as client:
        login(client)
        page = client.get("/search", params={"q": "orçamento"}).text
    assert f"/jobs/{job_id}" in page
    # Assert on a distinctive neighbor word that can ONLY come from the rendered
    # snippet (the query itself is echoed in the form/placeholder, so checking it
    # would be tautological).
    assert "cronograma" in page


def test_search_does_not_leak_other_users_transcripts(tmp_path):
    worker = build_memory_repositories()
    _seed_transcript(worker, "segredo do outro usuário sobre orçamento", user_id=2)
    app = build_app(tmp_path, worker=worker)
    with TestClient(app) as client:
        login(client)  # admin is user id=1
        page = client.get("/search", params={"q": "orçamento"}).text
    assert "Nenhuma transcrição encontrada" in page
    assert "segredo do outro" not in page


def test_search_empty_query_renders_form(tmp_path):
    worker = build_memory_repositories()
    app = build_app(tmp_path, worker=worker)
    with TestClient(app) as client:
        login(client)
        response = client.get("/search")
    assert response.status_code == 200
    assert "Buscar transcrições" in response.text
