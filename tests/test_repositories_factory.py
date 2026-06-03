import pytest

from app.repositories import RepositoryBackendError, build_repositories
from app.repositories.memory import InMemoryJobRepository


def test_memory_backend_builds_in_memory_repositories():
    repos = build_repositories("memory")
    assert isinstance(repos.jobs, InMemoryJobRepository)


def test_default_backend_is_postgres_and_fails_clearly_when_not_integrated():
    # No backend argument -> default 'postgres'. On this branch the Postgres
    # adapter does not exist yet, so it must fail with a clear, actionable error.
    with pytest.raises(RepositoryBackendError) as exc:
        build_repositories(None)
    message = str(exc.value)
    assert "postgres-core" in message
    assert "memory" in message  # mentions the dev-only escape hatch


def test_explicit_postgres_backend_fails_clearly():
    with pytest.raises(RepositoryBackendError):
        build_repositories("postgres")


def test_unknown_backend_is_rejected():
    with pytest.raises(RepositoryBackendError):
        build_repositories("mysql")
