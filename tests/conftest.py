"""Shared test fixtures.

Persistence tests run against a real PostgreSQL database. The URL is taken from
``TEST_DATABASE_URL`` (falling back to ``DATABASE_URL`` and then a localhost
default). When no PostgreSQL is reachable, the database fixtures ``skip`` — they
never silently fall back to SQLite.

To run the database tests, point ``TEST_DATABASE_URL`` at a disposable database,
for example::

    docker run -d --name meet_pg_test \\
        -e POSTGRES_DB=meet_test -e POSTGRES_USER=meet_user \\
        -e POSTGRES_PASSWORD=meet_password -p 55432:5432 postgres:16
    export TEST_DATABASE_URL=postgresql+psycopg://meet_user:meet_password@localhost:55432/meet_test
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from app.database import models
from app.database import session as db_session
from app.database.connection import normalize_database_url

DEFAULT_TEST_URL = "postgresql+psycopg://meet_user:meet_password@localhost:5432/meet_test"


def _test_database_url() -> str:
    raw = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or DEFAULT_TEST_URL
    )
    return normalize_database_url(raw).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def engine():
    """Session-wide engine bound to the test database; skips if unreachable.

    Also binds the application's global engine (used by ``get_db`` and
    ``session_scope``) to the test database so web routes and background jobs hit
    the same schema.
    """
    url = _test_database_url()
    eng = create_engine(url, future=True, pool_pre_ping=True)
    try:
        connection = eng.connect()
        connection.close()
    except Exception as exc:  # noqa: BLE001 - any connection failure means skip.
        eng.dispose()
        pytest.skip(f"PostgreSQL not available at {url!r}: {exc}")

    models.Base.metadata.drop_all(eng)
    models.Base.metadata.create_all(eng)
    db_session.init_engine(engine=eng)
    try:
        yield eng
    finally:
        models.Base.metadata.drop_all(eng)
        db_session.reset_engine()
        eng.dispose()


def _truncate_all(eng) -> None:
    tables = ", ".join(table.name for table in models.Base.metadata.sorted_tables)
    with eng.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture()
def pg(engine):
    """Ensure the schema exists and truncate every table after the test.

    Use for tests that touch the database through the application (TestClient,
    background jobs) and manage their own sessions.
    """
    try:
        yield engine
    finally:
        _truncate_all(engine)


@pytest.fixture()
def db(engine):
    """A clean ORM session per test; truncates all tables afterward."""
    session = db_session.get_sessionmaker()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        _truncate_all(engine)
