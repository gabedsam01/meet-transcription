"""Pure-logic tests for DATABASE_URL parsing/validation (no database needed)."""

import pytest

from app.database.connection import (
    DatabaseConfigError,
    get_database_url,
    normalize_database_url,
)


def test_get_database_url_requires_the_variable():
    with pytest.raises(DatabaseConfigError, match="DATABASE_URL is required"):
        get_database_url({})


def test_get_database_url_rejects_blank_value():
    with pytest.raises(DatabaseConfigError, match="DATABASE_URL is required"):
        get_database_url({"DATABASE_URL": "   "})


def test_get_database_url_rejects_non_postgres_backend():
    with pytest.raises(DatabaseConfigError, match="must be a PostgreSQL URL"):
        get_database_url({"DATABASE_URL": "sqlite:////app/data/app.db"})


def test_get_database_url_passes_through_psycopg_url():
    url = get_database_url(
        {"DATABASE_URL": "postgresql+psycopg://u:p@host:5432/db"}
    )
    assert url == "postgresql+psycopg://u:p@host:5432/db"


def test_normalize_defaults_driver_to_psycopg():
    url = normalize_database_url("postgresql://u:p@host:5432/db")
    assert url.drivername == "postgresql+psycopg"


def test_normalize_rejects_garbage():
    with pytest.raises(DatabaseConfigError):
        get_database_url({"DATABASE_URL": "not-a-url::::"})
