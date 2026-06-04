"""Database connection helpers.

This module is responsible for one thing: turning the ``DATABASE_URL``
environment variable into a validated PostgreSQL SQLAlchemy URL and an engine.
It does not hold any global state — see ``app.database.session`` for that.

SQLite is no longer supported; ``DATABASE_URL`` must be a PostgreSQL URL.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL, make_url

DATABASE_URL_ENV = "DATABASE_URL"


class DatabaseConfigError(RuntimeError):
    """Raised when ``DATABASE_URL`` is missing or not a PostgreSQL URL."""


def get_database_url(env: Mapping[str, str] | None = None) -> str:
    """Return the normalized PostgreSQL URL from the environment.

    Raises ``DatabaseConfigError`` with an actionable message when the variable
    is absent or points at a non-PostgreSQL backend.
    """
    values = env if env is not None else os.environ
    raw = (values.get(DATABASE_URL_ENV) or "").strip()
    if not raw:
        raise DatabaseConfigError(
            "DATABASE_URL is required. Set it to a PostgreSQL URL, e.g. "
            "postgresql+psycopg://user:password@host:5432/dbname"
        )
    return normalize_database_url(raw).render_as_string(hide_password=False)


def normalize_database_url(raw: str) -> URL:
    """Parse ``raw`` into a PostgreSQL URL, defaulting the driver to psycopg 3.

    ``postgresql://...`` (no driver) is normalized to ``postgresql+psycopg://``
    so we never accidentally fall back to the uninstalled psycopg2 driver.
    """
    try:
        url = make_url(raw)
    except Exception as exc:  # noqa: BLE001 - re-raise as a clear config error.
        raise DatabaseConfigError(f"DATABASE_URL is not a valid database URL: {exc}") from exc

    if url.get_backend_name() != "postgresql":
        raise DatabaseConfigError(
            "DATABASE_URL must be a PostgreSQL URL "
            f"(got backend {url.get_backend_name()!r}). "
            "SQLite and other databases are no longer supported."
        )
    if "+" not in url.drivername:
        # No explicit driver (e.g. "postgresql://") — default to psycopg 3
        # instead of letting SQLAlchemy fall back to the uninstalled psycopg2.
        url = url.set(drivername="postgresql+psycopg")
    return url


def create_engine_from_url(url: str | URL, **kwargs: Any) -> Engine:
    """Create a SQLAlchemy engine with project defaults."""
    options: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    options.update(kwargs)
    return create_engine(url, **options)


def create_database_engine(env: Mapping[str, str] | None = None, **kwargs: Any) -> Engine:
    """Create the engine from ``DATABASE_URL`` in the environment."""
    return create_engine_from_url(get_database_url(env), **kwargs)
