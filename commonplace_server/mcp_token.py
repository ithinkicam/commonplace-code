"""MCP URL-path secret token resolution for the Commonplace MCP server.

The token is a ≥32-byte urlsafe random string stored in macOS login keychain at
service=commonplace-mcp-token, account=mcp.  It is mounted as the URL path prefix
so that ``/mcp/<token>`` is the only valid MCP endpoint (bare ``/mcp`` returns 404).

Resolution order:
  1. COMMONPLACE_MCP_TOKEN environment variable (useful for CI / local overrides)
  2. macOS keychain item: service=commonplace-mcp-token, account=mcp

If neither is available, ``resolve_mcp_token`` returns ``None`` and the server
refuses to start — call ``make mcp-token-init`` to seed the keychain entry.
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_KEYCHAIN_SERVICE = "commonplace-mcp-token"
_KEYCHAIN_ACCOUNT = "mcp"


def resolve_mcp_token() -> str | None:
    """Resolve the MCP URL-path secret token.

    Checks ``COMMONPLACE_MCP_TOKEN`` env var first, then falls back to
    reading from the macOS keychain via ``security find-generic-password``.

    Returns
    -------
    The token string, or ``None`` if neither source is available.
    """
    env_val = os.environ.get("COMMONPLACE_MCP_TOKEN")
    if env_val:
        return env_val

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                _KEYCHAIN_SERVICE,
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            if token:
                return token
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("MCP token keychain lookup failed: %s", exc)

    return None
