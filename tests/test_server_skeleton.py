"""Tests for the Commonplace MCP server skeleton (task 1_2_mcp_skeleton).

Three concerns:
1. The healthcheck MCP tool returns the expected payload shape.
2. HTTP GET /healthcheck returns 200 with the correct JSON shape.
3. Importing the server module has no network/DB side effects.
"""

from __future__ import annotations

import os
from typing import Any

from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_KEYS = {"status", "service", "version", "schema_version", "timestamp"}


def _assert_health_payload(payload: dict[str, Any]) -> None:
    assert set(payload.keys()) >= EXPECTED_KEYS, f"Missing keys: {EXPECTED_KEYS - set(payload.keys())}"
    assert payload["status"] == "ok"
    assert payload["service"] == "commonplace"
    assert isinstance(payload["version"], str)
    assert isinstance(payload["schema_version"], int)
    assert isinstance(payload["timestamp"], str)


# ---------------------------------------------------------------------------
# Test 1: module import has no side effects
# ---------------------------------------------------------------------------


def test_import_no_side_effects() -> None:
    """Importing server module must not open network connections or touch the DB."""
    # If this import raises (e.g. tries to bind a port), the test fails.
    import commonplace_server.server as server_mod  # noqa: PLC0415

    # The mcp app should be constructed but the server must not be running.
    assert server_mod.mcp is not None
    assert server_mod.mcp.name == "commonplace"


# ---------------------------------------------------------------------------
# Test 2: healthcheck MCP tool (in-process, no live server)
# ---------------------------------------------------------------------------


def test_healthcheck_tool_returns_expected_keys(tmp_path: Any) -> None:
    """The healthcheck tool should return a dict with expected keys and status == 'ok'."""

    db_file = str(tmp_path / "test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import healthcheck

        result = healthcheck()
        _assert_health_payload(result)
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


# ---------------------------------------------------------------------------
# Test 3: HTTP /healthcheck endpoint (ASGI test client — no live server)
# ---------------------------------------------------------------------------


def test_http_healthcheck_returns_200(tmp_path: Any) -> None:
    """HTTP GET /healthcheck must return 200 with correct JSON shape."""

    db_file = str(tmp_path / "http_test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import mcp

        app = mcp.http_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.get("/healthcheck")

        assert response.status_code == 200
        payload = response.json()
        _assert_health_payload(payload)
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]
