#!/usr/bin/env python3
"""Initialize the MCP URL-path secret token in macOS login keychain.

Generates a ≥32-byte urlsafe random token via secrets.token_urlsafe(32),
stores it at keychain service=commonplace-mcp-token, account=mcp.

Idempotent: if a token already exists, prints it rather than overwriting.

Also writes .mcp.json at the repo root with the token-suffixed URL so that
the Claude Code CLI picks up the new endpoint on next restart.

Usage:
    python scripts/init_mcp_token.py
    make mcp-token-init
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from pathlib import Path

KEYCHAIN_SERVICE = "commonplace-mcp-token"
KEYCHAIN_ACCOUNT = "mcp"

MCP_HOST = "127.0.0.1"
MCP_PORT = 8765

REPO_ROOT = Path(__file__).parent.parent
MCP_JSON_PATH = REPO_ROOT / ".mcp.json"


def _read_existing_token() -> str | None:
    """Return the existing keychain token, or None if absent."""
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
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
    return None


def _store_token(token: str) -> None:
    """Store *token* in the macOS login keychain (add or update)."""
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",  # update if already exists
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


def write_mcp_json(token: str) -> None:
    """Write .mcp.json atomically with the token-suffixed URL."""
    url = f"http://{MCP_HOST}:{MCP_PORT}/mcp/{token}/"
    config = {
        "mcpServers": {
            "commonplace": {
                "type": "http",
                "url": url,
            }
        }
    }
    # Atomic write via temp file + rename
    tmp_path = MCP_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(MCP_JSON_PATH)
    print(f"Wrote {MCP_JSON_PATH} → {url}")


def main() -> None:
    existing = _read_existing_token()

    if existing:
        print("Token already exists in keychain — not overwriting.")
        print(f"  Service : {KEYCHAIN_SERVICE}")
        print(f"  Account : {KEYCHAIN_ACCOUNT}")
        print(f"  Token   : {existing}")
        url = f"http://{MCP_HOST}:{MCP_PORT}/mcp/{existing}/"
        print(f"  MCP URL : {url}")
        # Still regenerate .mcp.json in case it was lost or points at old URL
        write_mcp_json(existing)
        return

    token = secrets.token_urlsafe(32)
    _store_token(token)
    print("Generated and stored new MCP token in keychain.")
    print(f"  Service : {KEYCHAIN_SERVICE}")
    print(f"  Account : {KEYCHAIN_ACCOUNT}")
    print(f"  Token   : {token}")
    url = f"http://{MCP_HOST}:{MCP_PORT}/mcp/{token}/"
    print(f"  MCP URL : {url}")
    write_mcp_json(token)
    print()
    print("Next: restart the MCP server so it picks up the new token:")
    print("  launchctl kickstart -k gui/$UID/com.commonplace.mcp-server")
    print("Then restart the Claude Code CLI from the repo root to pick up .mcp.json.")


if __name__ == "__main__":
    main()
