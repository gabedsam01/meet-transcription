import pytest

from app.database.connection import DatabaseConfigError
from app.repositories import RepositoryBackendError, build_repositories
from app.repositories.memory import InMemoryJobRepository
from app.repositories.postgres import PgJobRepository

_FAKE_URL = "postgresql+psycopg://u:p@localhost:5432/db"


def test_memory_backend_builds_in_memory_repositories():
    repos = build_repositories("memory")
    assert isinstance(repos.jobs, InMemoryJobRepository)


def test_default_backend_is_postgres(monkeypatch):
    # No backend argument -> default 'postgres'. postgres-core is integrated, so a
    # valid DATABASE_URL yields the Postgres-backed bundle (the engine is lazy, so
    # no live connection is needed to build it).
    monkeypatch.setenv("DATABASE_URL", _FAKE_URL)
    repos = build_repositories(None)
    assert isinstance(repos.jobs, PgJobRepository)


def test_explicit_postgres_backend_builds(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _FAKE_URL)
    repos = build_repositories("postgres")
    assert isinstance(repos.jobs, PgJobRepository)


def test_postgres_backend_without_database_url_fails_clearly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DatabaseConfigError):
        build_repositories("postgres")


def test_unknown_backend_is_rejected():
    with pytest.raises(RepositoryBackendError):
        build_repositories("mysql")
