"""Job-queue helper functions for the Commonplace MCP server.

Pure functions that accept a sqlite3.Connection and perform job-queue
operations against the ``job_queue`` table.  These are intentionally
decoupled from MCP so they can be unit-tested without FastMCP plumbing.

Public API
----------
submit(conn, kind, payload)  -> dict
status(conn, job_id)         -> dict
cancel(conn, job_id)         -> dict
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


def submit(conn: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Insert a new job_queue row with status 'queued'.

    Parameters
    ----------
    conn:
        Open SQLite connection.
    kind:
        Non-empty string ≤ 64 characters identifying the job type.
    payload:
        A JSON-serialisable dict of handler-specific parameters.

    Returns
    -------
    dict with keys ``id``, ``status``, ``kind``.

    Raises
    ------
    ValueError
        If ``kind`` is empty, exceeds 64 characters, ``payload`` is not a
        dict, or ``payload`` is not JSON-serialisable.
    """
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("kind must be a non-empty string")
    if len(kind) > 64:
        raise ValueError(f"kind must be ≤ 64 characters, got {len(kind)}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    try:
        payload_json = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload is not JSON-serialisable: {exc}") from exc

    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO job_queue (kind, payload, status, attempts, created_at)
            VALUES (?, ?, 'queued', 0, ?)
            """,
            (kind, payload_json, now_iso),
        )
    job_id = cursor.lastrowid
    return {"id": job_id, "status": "queued", "kind": kind}


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    """Return the job_queue row for *job_id* as a plain dict.

    The ``payload`` field is JSON-decoded before returning.

    Raises
    ------
    ValueError
        If no row with the given id exists.
    """
    row = conn.execute(
        """
        SELECT id, kind, status, created_at, started_at, completed_at,
               error, attempts, payload
          FROM job_queue
         WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"job {job_id} not found")

    result: dict[str, Any] = dict(row)
    with contextlib.suppress(TypeError, json.JSONDecodeError):
        result["payload"] = json.loads(result["payload"])
    return result


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


def cancel(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    """Atomically cancel a job if it is in a cancellable state.

    Cancellable states: ``queued`` or ``running``.
    Terminal states (``complete``, ``failed``, ``cancelled``) are left
    unchanged and ``cancelled=False`` is returned.

    Returns
    -------
    dict with keys ``id``, ``cancelled`` (bool), ``previous_status`` (str).

    Raises
    ------
    ValueError
        If no row with the given id exists.
    """
    # First check the row exists and grab current status.
    existing = conn.execute(
        "SELECT status FROM job_queue WHERE id = ?",
        (job_id,),
    ).fetchone()
    if existing is None:
        raise ValueError(f"job {job_id} not found")

    previous_status: str = existing["status"]

    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        cursor = conn.execute(
            """
            UPDATE job_queue
               SET status       = 'cancelled',
                   completed_at = ?
             WHERE id = ?
               AND status IN ('queued', 'running')
            """,
            (now_iso, job_id),
        )
    cancelled = cursor.rowcount > 0
    return {"id": job_id, "cancelled": cancelled, "previous_status": previous_status}
