"""Capture endpoint logic for the Commonplace MCP server.

Handles bearer-auth, inbox file writes, and job enqueue for POST /capture.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import commonplace_server.jobs as jobs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bearer token resolution
# ---------------------------------------------------------------------------


def resolve_bearer() -> str | None:
    """Resolve the capture bearer token.

    Checks ``COMMONPLACE_CAPTURE_BEARER`` env var first, then falls back to
    reading from the macOS keychain via ``security find-generic-password``.

    Returns
    -------
    The bearer token string, or ``None`` if neither source is available.
    """
    env_val = os.environ.get("COMMONPLACE_CAPTURE_BEARER")
    if env_val:
        return env_val

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "commonplace-capture-bearer",
                "-a",
                "capture",
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
        logger.debug("Keychain lookup failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# CaptureRequest dataclass
# ---------------------------------------------------------------------------


@dataclass
class CaptureRequest:
    """Validated capture request body."""

    source: str
    kind: str
    content: str
    metadata: dict[str, Any] | None = field(default=None)


# ---------------------------------------------------------------------------
# handle_capture
# ---------------------------------------------------------------------------


def handle_capture(
    body: dict[str, Any],
    authorization: str | None,
    *,
    conn: sqlite3.Connection,
    inbox_dir: Path,
    expected_bearer: str | None,
) -> tuple[int, dict[str, Any]]:
    """Process a capture request.

    Parameters
    ----------
    body:
        Parsed JSON dict from the request body.
    authorization:
        Value of the ``Authorization`` header (e.g. ``"Bearer <token>"``).
    conn:
        Open SQLite connection for job enqueue.
    inbox_dir:
        Directory where inbox files are written. Created if missing.
    expected_bearer:
        The expected bearer token. If ``None``, the server was not configured
        with a token and every request is rejected with 503.

    Returns
    -------
    ``(status_code, json_body)`` tuple.
    """
    # 503 — no bearer configured at server startup
    if expected_bearer is None:
        return 503, {"error": "capture endpoint not configured: no bearer token available"}

    # 401 — missing or wrong bearer
    if not authorization:
        return 401, {"error": "missing Authorization header"}
    if not authorization.startswith("Bearer "):
        return 401, {"error": "invalid Authorization header format"}
    provided_token = authorization[len("Bearer "):]
    if provided_token != expected_bearer:
        return 401, {"error": "invalid bearer token"}

    # 400 — validate required fields
    missing = [f for f in ("source", "kind", "content") if not body.get(f)]
    if missing:
        return 400, {"error": f"missing required fields: {', '.join(missing)}"}

    source = body["source"]
    kind = body["kind"]
    content = body["content"]
    metadata = body.get("metadata")

    if not isinstance(source, str) or not source:
        return 400, {"error": "field 'source' must be a non-empty string"}
    if not isinstance(kind, str) or not kind:
        return 400, {"error": "field 'kind' must be a non-empty string"}
    if not isinstance(content, str) or not content:
        return 400, {"error": "field 'content' must be a non-empty string"}
    if metadata is not None and not isinstance(metadata, dict):
        return 400, {"error": "field 'metadata' must be a dict or null"}

    # Build the record to write
    request_obj = CaptureRequest(
        source=source,
        kind=kind,
        content=content,
        metadata=metadata,
    )
    record: dict[str, Any] = {
        "source": request_obj.source,
        "kind": request_obj.kind,
        "content": request_obj.content,
    }
    if request_obj.metadata is not None:
        record["metadata"] = request_obj.metadata

    # Compute filename components
    body_bytes = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    short_hash = hashlib.sha1(body_bytes).hexdigest()[:8]
    now_utc = datetime.now(UTC)
    timestamp = now_utc.strftime("%Y-%m-%dT%H%M%SZ")
    filename = f"{timestamp}_{short_hash}.json"

    # Write atomically: .tmp + fsync + rename
    inbox_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = inbox_dir / f"{filename}.tmp"
    final_path = inbox_dir / filename

    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.rename(final_path)
    except OSError as exc:
        logger.error("Failed to write inbox file %s: %s", final_path, exc)
        return 500, {"error": "failed to write inbox file"}

    # Enqueue job
    job_result = jobs.submit(conn, "capture", {"inbox_file": filename})
    job_id: int = job_result["id"]

    return 202, {
        "status": "accepted",
        "job_id": job_id,
        "inbox_file": filename,
    }
