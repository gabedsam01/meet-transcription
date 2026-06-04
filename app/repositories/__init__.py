from __future__ import annotations

from app.core.ports import Repositories


class RepositoryBackendError(RuntimeError):
    """Raised when the requested repository backend cannot be built."""


def build_repositories(backend: str | None = None) -> Repositories:
    """Build the repository bundle for the selected backend.

    Default is 'postgres' (production). 'memory' is for tests, local smoke runs
    and development only, and is forbidden in production.
    """
    selected = (backend or "postgres").strip().lower() or "postgres"

    if selected == "memory":
        from app.repositories.memory import build_memory_repositories

        return build_memory_repositories()

    if selected == "postgres":
        try:
            from app.repositories.postgres import build_postgres_repositories
        except ImportError as exc:
            raise RepositoryBackendError(
                "WORKER_REPOSITORY_BACKEND=postgres but the PostgreSQL adapter is not "
                "available on this branch. The real repositories are delivered by "
                "feat/postgres-core; merge/integrate that branch before running against "
                "PostgreSQL. For local development only (never production) set "
                "WORKER_REPOSITORY_BACKEND=memory."
            ) from exc
        return build_postgres_repositories()

    raise RepositoryBackendError(
        f"Unknown WORKER_REPOSITORY_BACKEND={backend!r}; use 'postgres' (default) "
        f"or 'memory' (development/tests only)."
    )
