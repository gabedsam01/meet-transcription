from __future__ import annotations

import logging

from app.observability import configure_logging


def setup_logging() -> None:
    """Configure root logging once for the process.

    Delegates to :func:`app.observability.configure_logging`, which honors
    ``LOG_FORMAT`` (``text`` default, or ``json`` for structured ingestion) and is
    idempotent — the web app factory and the worker both call this at startup.
    """
    configure_logging(level=logging.INFO)
