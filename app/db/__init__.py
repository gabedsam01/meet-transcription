"""Compatibility bridge package.

``app/db/postgres.py`` exposes the PostgreSQL repository bundle in the shape the
``feat/auth-users-settings`` branch consumes
(``build_repositories(database_url) -> RepositoryBundle``), wrapping the
canonical ``app/database/`` layer.
"""
