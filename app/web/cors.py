"""CORS middleware scoped to the Chrome extension recording endpoints.

The extension runs as ``chrome-extension://<id>`` and the browser enforces CORS
on the upload preflight. We MUST allow the extension's origin explicitly and
nothing else: a wildcard would leak Drive/Download endpoints to random sites.

Scoping:
- Only the API routes under ``/api/recordings/*`` get the CORS headers.
- Allowed methods are limited to the ones those routes need.
- The ``Authorization`` and ``X-Upload-Token`` headers are whitelisted.
- Credentials are never sent (the extension does not have cookies anyway).

Allowed origins:
- ``chrome-extension://<32 lowercase letters>`` (Chrome's extension id format).
- ``null`` is intentionally NOT allowed (would let ``data:`` / ``file:`` bypass
  the check); any other origin is rejected.
"""
from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Chrome extension ids are 32 lowercase letters. We anchor with ^ and $ so a
# crafted ``chrome-extension://abcd...evil.example`` is not matched.
_CHROME_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-z]{32}$")

# Scoped: the upload + ping endpoints only. Drive/Download are session-cookie
# flows, never used by the extension, and remain locked to the app origin.
_RECORDINGS_PREFIX = "/api/recordings/"

# Methods the recording routes accept. We do not advertise GET so a malicious
# site cannot preflight a Drive/Download URL with the chrome-extension origin.
_ALLOWED_METHODS = "GET, POST, OPTIONS"

# The extension may send these headers; we whitelist them so the preflight is
# deterministic and can't be used to probe internal headers.
_ALLOWED_HEADERS = (
    "Authorization, Content-Type, X-Upload-Token, X-Requested-With"
)

# We don't echo Vary on user-defined content; the Access-Control-Allow-Origin
# varies with the Origin header, so Vary: Origin is required for shared caches.
_VARY = "Origin"


def _is_recordings_path(path: str) -> bool:
    return path == _RECORDINGS_PREFIX.rstrip("/") or path.startswith(_RECORDINGS_PREFIX)


def _allowed_origin(origin: str) -> str | None:
    if not origin:
        return None
    if _CHROME_EXTENSION_ORIGIN.match(origin):
        return origin
    return None


class ChromeExtensionCORSMiddleware:
    """ASGI middleware that adds Chrome-extension-safe CORS headers.

    The middleware is intentionally tiny and dependency-free: it does not try to
    be a full CORS implementation. It just makes the browser happy for the two
    routes the extension needs (ping, upload) and rejects everything else with
    no CORS headers at all.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "").upper()
        headers = dict(scope.get("headers", []))
        origin_bytes = headers.get(b"origin")
        origin = origin_bytes.decode("latin-1") if origin_bytes else ""
        allowed = _allowed_origin(origin) if _is_recordings_path(path) else None

        if method == "OPTIONS":
            # Preflight: always answer from this middleware when an extension
            # origin is whitelisted; otherwise pass through (no CORS headers).
            if allowed is not None:
                await self._send_preflight(send, allowed)
                return
            await self.app(scope, receive, send)
            return

        if allowed is not None:
            # Wrap send so the actual response gets the CORS headers.
            async def send_with_cors(message: Message) -> None:
                if message["type"] == "http.response.start":
                    response_headers = list(message.get("headers", []))
                    response_headers.append(
                        (b"access-control-allow-origin", allowed.encode("latin-1"))
                    )
                    response_headers.append((b"vary", _VARY.encode("latin-1")))
                    message = {**message, "headers": response_headers}
                await send(message)
            await self.app(scope, receive, send_with_cors)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_preflight(send: Send, allowed_origin: str) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", allowed_origin.encode("latin-1")),
                    (b"access-control-allow-methods", _ALLOWED_METHODS.encode("latin-1")),
                    (b"access-control-allow-headers", _ALLOWED_HEADERS.encode("latin-1")),
                    (b"access-control-max-age", b"3600"),
                    (b"vary", _VARY.encode("latin-1")),
                    (b"content-length", b"0"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})


__all__ = ["ChromeExtensionCORSMiddleware"]
