"""Commonplace MCP server — FastMCP app with healthcheck tool and HTTP route.

Bind address defaults to 127.0.0.1:8765 (Tailscale terminates locally).
Override via COMMONPLACE_HOST / COMMONPLACE_PORT environment variables.

TODO task 1.6: add /capture HTTP route and submit_job MCP tool here once the
Plex Funnel collision (task 1.5) is resolved.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import commonplace_db
import commonplace_server.jobs as jobs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastMCP app instance (module-level, but no network/DB side effects on import)
# ---------------------------------------------------------------------------

mcp: FastMCP = FastMCP(name="commonplace")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_version() -> str:
    """Return the package version from package metadata."""
    try:
        return importlib.metadata.version("commonplace")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.1"


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Run migrations against *conn* and return the resulting schema version."""
    return commonplace_db.migrate(conn)


def _build_health_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "commonplace",
        "version": _get_version(),
        "schema_version": _get_schema_version(conn),
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


def healthcheck() -> dict[str, Any]:
    """Return service health, version, and DB schema version."""
    db_path = os.environ.get(
        "COMMONPLACE_DB_PATH",
        commonplace_db.DB_PATH,
    )
    conn = commonplace_db.connect(db_path)
    try:
        return _build_health_payload(conn)
    finally:
        conn.close()


# Register the function as an MCP tool.
# mcp.tool(fn) accepts a bare function and returns a FunctionTool;
# the original `healthcheck` name stays callable for tests.
mcp.tool(healthcheck)


# ---------------------------------------------------------------------------
# Job-queue MCP tools
# ---------------------------------------------------------------------------


def submit_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a new job to the job queue.

    Parameters
    ----------
    kind:
        Non-empty string ≤ 64 characters identifying the job type.
    payload:
        A JSON-serialisable dict of handler-specific parameters.

    Returns
    -------
    ``{"id": <int>, "status": "queued", "kind": kind}``
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return jobs.submit(conn, kind, payload)
    finally:
        conn.close()


mcp.tool(submit_job)


def get_job_status(job_id: int) -> dict[str, Any]:
    """Return the current status and metadata for a job queue entry.

    Parameters
    ----------
    job_id:
        The integer primary key of the job_queue row.

    Returns
    -------
    Dict with keys ``id, kind, status, created_at, started_at,
    completed_at, error, attempts, payload`` (payload JSON-decoded).

    Raises
    ------
    ValueError
        If no job with the given id exists.
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return jobs.status(conn, job_id)
    finally:
        conn.close()


mcp.tool(get_job_status)


def cancel_job(job_id: int) -> dict[str, Any]:
    """Attempt to cancel a queued or running job.

    Parameters
    ----------
    job_id:
        The integer primary key of the job_queue row.

    Returns
    -------
    ``{"id": job_id, "cancelled": <bool>, "previous_status": <str>}``
    ``cancelled`` is ``True`` only when the status was actually changed.

    Raises
    ------
    ValueError
        If no job with the given id exists.
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return jobs.cancel(conn, job_id)
    finally:
        conn.close()


mcp.tool(cancel_job)


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthcheck", methods=["GET"])
async def http_healthcheck(request: Request) -> Response:
    """HTTP GET /healthcheck — returns the same payload as the MCP tool."""
    db_path = os.environ.get(
        "COMMONPLACE_DB_PATH",
        commonplace_db.DB_PATH,
    )
    conn = commonplace_db.connect(db_path)
    try:
        payload = _build_health_payload(conn)
    finally:
        conn.close()
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Server startup (called by __main__)
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the Commonplace MCP server.

    Runs DB migrations on boot; exits non-zero if migrations fail.
    """
    host = os.environ.get("COMMONPLACE_HOST", "127.0.0.1")
    port = int(os.environ.get("COMMONPLACE_PORT", "8765"))
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    )

    # Run migrations on startup so a fresh install initialises itself.
    try:
        conn = commonplace_db.connect(db_path)
        schema_ver = commonplace_db.migrate(conn)
        conn.close()
        logger.info(
            "commonplace startup: db=%s schema_version=%d", db_path, schema_ver
        )
    except Exception as exc:
        logger.error("DB migration failed — cannot start: %s", exc, exc_info=True)
        sys.exit(1)

    logger.info(
        "Starting Commonplace MCP server on http://%s:%d  (version %s)",
        host,
        port,
        _get_version(),
    )

    mcp.run(transport="http", host=host, port=port)
