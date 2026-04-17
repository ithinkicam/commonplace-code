#!/usr/bin/env python3
"""Rotate the MCP URL-path secret token.

Generates a fresh token via secrets.token_urlsafe(32), overwrites the keychain
entry at service=commonplace-mcp-token, account=mcp, rewrites .mcp.json, and
kicks the launchd service so the server immediately serves the new path.

Prints the new full MCP URL for pasting into claude.ai custom-connector config.

Usage:
    python scripts/rotate_mcp_token.py
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

KEYCHAIN_SERVICE = "commonplace-mcp-token"
KEYCHAIN_ACCOUNT = "mcp"

MCP_HOST = "127.0.0.1"
MCP_PORT = 8765
LAUNCHD_LABEL = "com.commonplace.mcp-server"

REPO_ROOT = Path(__file__).parent.parent
MCP_JSON_PATH = REPO_ROOT / ".mcp.json"


def _store_token(token: str) -> None:
    """Overwrite the keychain entry with *token* (add-generic-password -U)."""
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
            token,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        print(
            f"ERROR: Failed to store token in keychain: {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)


def _write_mcp_json(token: str) -> None:
    """Write .mcp.json atomically with the new token-suffixed URL."""
    url = f"http://{MCP_HOST}:{MCP_PORT}/mcp/{token}/"
    config = {
        "mcpServers": {
            "commonplace": {
                "type": "http",
                "url": url,
            }
        }
    }
    tmp_path = MCP_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(MCP_JSON_PATH)
    return url


def _kickstart_service() -> None:
    """Restart the launchd MCP server service via kickstart -k."""
    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{LAUNCHD_LABEL}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        print(
            f"WARNING: launchctl kickstart failed (exit {result.returncode}): "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        print(
            "The keychain and .mcp.json are updated — manually restart the server.",
            file=sys.stderr,
        )
    else:
        print(f"Restarted {LAUNCHD_LABEL} via launchctl kickstart.")


def main() -> None:
    token = secrets.token_urlsafe(32)
    _store_token(token)
    url = _write_mcp_json(token)

    print("MCP token rotated.")
    print(f"  Service : {KEYCHAIN_SERVICE}")
    print(f"  Account : {KEYCHAIN_ACCOUNT}")
    print(f"  Token   : {token}")
    print(f"  MCP URL : {url}")
    print()

    _kickstart_service()

    print()
    print("Paste this URL into the claude.ai custom-connector config:")
    print(f"  {url}")
    print()
    print("Then restart the Claude Code CLI from the repo root to pick up .mcp.json.")


if __name__ == "__main__":
    main()
