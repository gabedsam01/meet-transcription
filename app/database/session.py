"""Engine and session management.

Holds the process-wide SQLAlchemy engine and session factory. The engine is
created lazily so that importing this module never requires ``DATABASE_URL`` to
be set (tests point it at a test database before the first session is opened).

Usable from both FastAPI (``get_db`` dependency) and plain scripts/workers
(``session_scope`` context manager).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Mapping

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.connection import create_database_engine, create_engine_from_url

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def init_engine(
    url: str | None = None,
    *,
    engine: Engine | None = None,
    env: Mapping[str, str] | None = None,
) -> Engine:
    """(Re)initialize the global engine and session factory.

    Pass ``engine`` or ``url`` to bind a specific database (used by tests), or
    nothing to build the engine from ``DATABASE_URL``.
    """
    global _engine, _SessionLocal
    if engine is not None:
        _engine = engine
    elif url is not None:
        _engine = create_engine_from_url(url)
    else:
        _engine = create_database_engine(env)
    _SessionLocal = sessionmaker(
        bind=_engine, autoflush=False, expire_on_commit=False, future=True
    )
    return _engine


def get_engine() -> Engine:
    """Return the global engine, initializing it from the environment if needed."""
    if _engine is None:
        init_engine()
    assert _engine is not None  # for type checkers
    return _engine


def get_sessionmaker() -> sessionmaker:
    """Return the global session factory, initializing it if needed."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None  # for type checkers
    return _SessionLocal


def reset_engine() -> None:
    """Dispose of and clear the global engine (used between test runs)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield a session, committing on success.

    The session is committed when the request handler returns normally and
    rolled back if it raises, then always closed.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for background tasks and scripts.

    Commits on success, rolls back on error, always closes.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
