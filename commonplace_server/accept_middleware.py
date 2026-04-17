"""ASGI middleware that normalizes the inbound ``Accept`` header for MCP paths.

Workaround for python-sdk #2349: claude.ai's MCP client sends
``Accept: text/event-stream`` only, but the SDK's ``_validate_accept_header``
uses strict AND logic and returns 406 unless *both* ``application/json`` and
``text/event-stream`` are present.  We rewrite the header on the way in so the
spec-strict validator is satisfied; clients still receive the negotiated
response type.  Scope is limited to ``/mcp/...`` paths so ``/healthcheck`` and
``/capture`` are untouched.  Remove once the upstream fix ships.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_BOTH = b"application/json, text/event-stream"
_MCP_PREFIX = b"/mcp/"


def _accept_has_both(value: bytes) -> bool:
    lower = value.lower()
    return b"application/json" in lower and b"text/event-stream" in lower


class AcceptHeaderMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_path: bytes = scope.get("raw_path") or scope.get("path", "").encode("latin-1")
        if not raw_path.startswith(_MCP_PREFIX):
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
        new_headers: list[tuple[bytes, bytes]] = []
        saw_accept = False
        for name, value in headers:
            if name.lower() == b"accept":
                saw_accept = True
                if _accept_has_both(value):
                    new_headers.append((name, value))
                else:
                    new_headers.append((b"accept", _BOTH))
            else:
                new_headers.append((name, value))

        if not saw_accept:
            new_headers.append((b"accept", _BOTH))

        new_scope = dict(scope)
        new_scope["headers"] = new_headers
        await self.app(new_scope, receive, send)
