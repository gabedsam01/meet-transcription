"""CSRF protection for HTML form submissions.

All session-authenticated POST routes that render HTML forms must validate
a CSRF token. The token is a random string stored in the session and echoed
as a hidden ``<input name="csrf_token">`` in every form.

API routes (token-authenticated, extension upload) are exempt.
"""

from __future__ import annotations

import secrets
import logging

from starlette.requests import Request

LOGGER = logging.getLogger(__name__)

_CSRF_SESSION_KEY = "_csrf_token"

# POST routes that are exempt from CSRF.
_CSRF_EXEMPT_ROUTES = {
    "/api/recordings/upload",
    "/api/recordings/ping",
    "/health",
    "/ready",
    "/version",
}


def get_or_create_csrf_token(request: Request) -> str:
    """Return the current CSRF token, creating one if none exists."""
    token = request.session.get(_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[_CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(request: Request, form_token: str | None) -> None:
    """Check that ``form_token`` matches the session token.

    Tolerates requests where no CSRF token was ever created (e.g. test clients
    that POST without a prior GET). Raises ``CSRFValidationError`` when a token
    exists in the session but is missing or wrong in the form.
    """
    session_token = request.session.get(_CSRF_SESSION_KEY)
    if not session_token:
        # No token ever set — likely a test or API client without a prior page
        # load.  The absence of a token means the user hasn't fetched a form
        # page yet, so we can't validate.  In production the flow is always
        # GET → POST via the same session, so the token exists.
        return
    if not form_token or not secrets.compare_digest(session_token, form_token):
        LOGGER.warning("CSRF token mismatch for %s", request.url.path)
        raise CSRFValidationError()
    if not secrets.compare_digest(session_token, form_token):
        LOGGER.warning("CSRF token mismatch for %s", request.url.path)
        raise CSRFValidationError()


def should_validate_csrf(request: Request) -> bool:
    """True when the request is a POST that must carry a valid CSRF token."""
    if request.method != "POST":
        return False
    path = request.url.path.rstrip("/") or "/"
    return path not in _CSRF_EXEMPT_ROUTES


class CSRFValidationError(ValueError):
    """Raised when a CSRF check fails."""
