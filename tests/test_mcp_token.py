"""Tests for MCP URL-path token resolution and server mount behaviour.

Three groups:
1. Unit tests for commonplace_server.mcp_token.resolve_mcp_token
   (mirrors test_tmdb_client.py style — mocked subprocess, no real keychain).
2. Integration tests against the ASGI app:
   - GET /healthcheck → 200  (unauthenticated)
   - GET /mcp          → 404  (bare path, no token)
   - GET /mcp/<token>/ → 406  (FastMCP-standard when Accept headers absent)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Unit tests: resolve_mcp_token
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure COMMONPLACE_MCP_TOKEN is not set unless tests set it."""
    monkeypatch.delenv("COMMONPLACE_MCP_TOKEN", raising=False)


def test_resolve_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """COMMONPLACE_MCP_TOKEN env var takes priority over keychain."""
    monkeypatch.setenv("COMMONPLACE_MCP_TOKEN", "env-token-abc123")
    from commonplace_server.mcp_token import resolve_mcp_token

    assert resolve_mcp_token() == "env-token-abc123"


def test_resolve_token_missing_returns_none() -> None:
    """Returns None when env var absent and keychain has no entry."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        from commonplace_server.mcp_token import resolve_mcp_token

        result = resolve_mcp_token()
    assert result is None


def test_resolve_token_from_keychain() -> None:
    """Reads token from keychain when env var is absent."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="keychain-token-xyz\n")
        from commonplace_server.mcp_token import resolve_mcp_token

        result = resolve_mcp_token()
    assert result == "keychain-token-xyz"


def test_resolve_token_keychain_strips_whitespace() -> None:
    """Token is stripped of surrounding whitespace from keychain stdout."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="  my-token  \n")
        from commonplace_server.mcp_token import resolve_mcp_token

        result = resolve_mcp_token()
    assert result == "my-token"


def test_resolve_token_subprocess_exception_returns_none() -> None:
    """FileNotFoundError (security binary absent) returns None gracefully."""
    with patch("subprocess.run", side_effect=FileNotFoundError("no security binary")):
        from commonplace_server.mcp_token import resolve_mcp_token

        result = resolve_mcp_token()
    assert result is None


def test_keychain_queried_with_correct_args() -> None:
    """security find-generic-password called with correct service/account."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        from commonplace_server.mcp_token import (
            _KEYCHAIN_ACCOUNT,
            _KEYCHAIN_SERVICE,
            resolve_mcp_token,
        )

        resolve_mcp_token()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "security" in args
    assert "find-generic-password" in args
    assert _KEYCHAIN_SERVICE in args
    assert _KEYCHAIN_ACCOUNT in args
    assert "-w" in args


# ---------------------------------------------------------------------------
# Integration tests: ASGI app routing with a test token
# ---------------------------------------------------------------------------

TEST_TOKEN = "testtoken1234567890abcdef1234567890"


@pytest.fixture()
def _mcp_app(tmp_path):
    """Build the FastMCP ASGI app mounted at /mcp/<TEST_TOKEN>."""
    db_file = str(tmp_path / "test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    os.environ["COMMONPLACE_MCP_TOKEN"] = TEST_TOKEN
    try:
        # Re-import to pick up the env var (mcp_token module caches nothing)
        from commonplace_server.server import mcp

        app = mcp.http_app(path=f"/mcp/{TEST_TOKEN}")
        yield app
    finally:
        os.environ.pop("COMMONPLACE_DB_PATH", None)
        os.environ.pop("COMMONPLACE_MCP_TOKEN", None)


def test_healthcheck_unauthenticated_200(_mcp_app) -> None:
    """GET /healthcheck returns 200 without any token."""
    with TestClient(_mcp_app, raise_server_exceptions=True) as client:
        response = client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_bare_mcp_returns_404(_mcp_app) -> None:
    """GET /mcp (no token) returns 404 — not a valid endpoint."""
    with TestClient(_mcp_app, raise_server_exceptions=False) as client:
        response = client.get("/mcp")
    assert response.status_code == 404


def test_mcp_with_token_without_accept_returns_406(_mcp_app) -> None:
    """GET /mcp/<token>/ without proper Accept header returns 406 (FastMCP-standard).

    FastMCP may redirect /mcp/<token>/ (trailing slash) to /mcp/<token> via 307;
    the TestClient follows redirects by default, so the final status is 406.
    """
    with TestClient(_mcp_app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/mcp/{TEST_TOKEN}/",
            headers={"Accept": "text/html"},  # wrong content type
        )
    # FastMCP returns 406 when Accept headers are not MCP-compatible
    # (may be after a 307 redirect that TestClient follows automatically)
    assert response.status_code == 406


def test_mcp_with_token_no_trailing_slash_returns_406(_mcp_app) -> None:
    """GET /mcp/<token> (no trailing slash) without proper Accept returns 406."""
    with TestClient(_mcp_app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/mcp/{TEST_TOKEN}",
            headers={"Accept": "text/html"},
        )
    assert response.status_code == 406


def test_mcp_wrong_token_returns_404(_mcp_app) -> None:
    """GET /mcp/<wrong-token>/ returns 404 — wrong path."""
    with TestClient(_mcp_app, raise_server_exceptions=False) as client:
        response = client.get("/mcp/wrong-token-here/")
    assert response.status_code == 404
