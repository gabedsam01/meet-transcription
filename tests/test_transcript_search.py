from datetime import datetime, timezone

from app.repositories.memory import build_memory_repositories


def _now():
    return datetime.now(timezone.utc)


def _add(repos, user_id, text):
    job = repos.jobs.create_job(user_id, f"f{user_id}-{text[:3]}", "m.mp4", _now())
    repos.transcripts.create(job.id, user_id, text, None, None, _now())
    return job.id


def test_search_is_case_insensitive_substring_and_user_scoped():
    repos = build_memory_repositories()
    j1 = _add(repos, 1, "Discussão sobre o ORÇAMENTO anual")
    _add(repos, 1, "Planejamento de produto")
    _add(repos, 2, "orçamento secreto de outro usuário")

    results = repos.transcripts.search_transcripts(1, "orçamento")
    assert [t.job_id for t in results] == [j1]  # only this user's match


def test_search_blank_query_returns_empty():
    repos = build_memory_repositories()
    _add(repos, 1, "qualquer coisa")
    assert repos.transcripts.search_transcripts(1, "   ") == []


def test_search_limits_and_orders_newest_first():
    repos = build_memory_repositories()
    ids = [_add(repos, 1, f"reunião {i} importante") for i in range(3)]
    results = repos.transcripts.search_transcripts(1, "importante", limit=2)
    assert [t.job_id for t in results] == [ids[2], ids[1]]
