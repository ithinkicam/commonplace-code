"""Tests for AcceptHeaderMiddleware (Phase 6.C Accept-header fix).

Workaround for python-sdk #2349: claude.ai's MCP client sends a stripped
``Accept`` header and the SDK's strict validator returns 406.  This middleware
normalises the header before it reaches the validator, limited to ``/mcp/...``
paths so ``/healthcheck`` and ``/capture`` are unaffected.
"""

from __future__ import annotations

import json

import pytest
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from commonplace_server.accept_middleware import (
    _BOTH,
    AcceptHeaderMiddleware,
    _accept_has_both,
)

# ---------------------------------------------------------------------------
# Helpers: dummy ASGI app that echoes the inbound Accept header
# ---------------------------------------------------------------------------


class _EchoAcceptApp:
    """Dummy ASGI app that returns the inbound Accept header as the body."""

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        accept = b"<missing>"
        for name, value in scope.get("headers", []):
            if name.lower() == b"accept":
                accept = value
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": accept})


def _asgi_client(app) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Unit tests — header-level behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (b"application/json, text/event-stream", True),
        (b"text/event-stream, application/json", True),
        (b"APPLICATION/JSON, TEXT/EVENT-STREAM", True),
        (b"application/json", False),
        (b"text/event-stream", False),
        (b"*/*", False),
        (b"", False),
    ],
)
def test_accept_has_both(value: bytes, expected: bool) -> None:
    assert _accept_has_both(value) is expected


def test_mcp_path_sse_only_is_rewritten() -> None:
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    with _asgi_client(app) as client:
        resp = client.get("/mcp/tok123", headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200
    assert resp.content == _BOTH


def test_mcp_path_wildcard_is_rewritten() -> None:
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    with _asgi_client(app) as client:
        resp = client.get("/mcp/tok123", headers={"Accept": "*/*"})
    assert resp.content == _BOTH


def test_mcp_path_json_only_is_rewritten() -> None:
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    with _asgi_client(app) as client:
        resp = client.get("/mcp/tok123", headers={"Accept": "application/json"})
    assert resp.content == _BOTH


def test_mcp_path_missing_accept_gets_default() -> None:
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    # httpx always sets Accept; use a raw ASGI scope instead.
    import anyio

    received_body: dict[str, bytes] = {}

    async def run() -> None:
        messages: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg: dict) -> None:
            messages.append(msg)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp/tok123",
            "raw_path": b"/mcp/tok123",
            "query_string": b"",
            "headers": [],  # no Accept
        }
        await app(scope, receive, send)
        for msg in messages:
            if msg["type"] == "http.response.body":
                received_body["b"] = msg["body"]

    anyio.run(run)
    assert received_body["b"] == _BOTH


def test_mcp_path_both_present_is_passthrough() -> None:
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    with _asgi_client(app) as client:
        resp = client.get(
            "/mcp/tok123",
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert resp.content == b"application/json, text/event-stream"


def test_non_mcp_path_is_untouched() -> None:
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    with _asgi_client(app) as client:
        resp = client.get("/healthcheck", headers={"Accept": "text/event-stream"})
    assert resp.content == b"text/event-stream"


def test_capture_path_is_untouched() -> None:
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    with _asgi_client(app) as client:
        resp = client.get("/capture", headers={"Accept": "application/json"})
    assert resp.content == b"application/json"


def test_bare_mcp_path_is_untouched() -> None:
    """``/mcp`` without a token is a 404 path; middleware must leave it alone."""
    app = AcceptHeaderMiddleware(_EchoAcceptApp())
    with _asgi_client(app) as client:
        resp = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert resp.content == b"text/event-stream"


# ---------------------------------------------------------------------------
# Integration test — middleware wired through FastMCP's http_app
# ---------------------------------------------------------------------------


def test_integration_sse_only_accept_completes_initialize(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """POST /mcp/<token> with ``Accept: text/event-stream`` must return 200.

    Without the middleware, the SDK's ``_validate_accept_header`` returns 406
    because the header lacks ``application/json``.
    """
    import os

    os.environ["COMMONPLACE_DB_PATH"] = str(tmp_path / "it.db")
    try:
        from commonplace_server.server import mcp

        app = mcp.http_app(
            path="/mcp/testtoken",
            middleware=[Middleware(AcceptHeaderMiddleware)],
        )
        with TestClient(app, raise_server_exceptions=True) as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-ai-repro", "version": "0.1"},
                },
            }
            resp = client.post(
                "/mcp/testtoken",
                headers={
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload),
            )
        assert resp.status_code == 200, (
            f"expected 200 but got {resp.status_code}: {resp.text[:400]}"
        )
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_integration_sse_only_without_middleware_still_406(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Sanity check: without the middleware, SDK returns 406 for SSE-only Accept.

    Guards the test above against an upstream fix silently making the
    workaround redundant — if this ever starts passing 200, revisit whether the
    middleware can be removed.
    """
    import os

    os.environ["COMMONPLACE_DB_PATH"] = str(tmp_path / "it2.db")
    try:
        from commonplace_server.server import mcp

        app = mcp.http_app(path="/mcp/testtoken")  # no middleware
        with TestClient(app, raise_server_exceptions=True) as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-ai-repro", "version": "0.1"},
                },
            }
            resp = client.post(
                "/mcp/testtoken",
                headers={
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload),
            )
        assert resp.status_code == 406, (
            f"expected 406 (upstream strict validator) but got {resp.status_code}: "
            f"{resp.text[:400]} — if this is now 200, upstream may have fixed #2349"
        )
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]
