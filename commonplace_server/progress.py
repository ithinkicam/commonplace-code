"""Embedding pipeline progress reporting.

report(conn, content_type=..., recent_limit=...) returns a snapshot combining:
  - aggregate document counts (by status + by content_type)
  - the oldest still-pending document
  - currently running ingest_* jobs ("what is embedding right now")
  - the most recent ingest_* jobs to finish ("what last finished")
  - throughput over the last hour

Decoupled from FastMCP so it can be unit-tested directly.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

# Payload keys consulted (in order) when building a one-line job summary.
_SUMMARY_KEYS = ("title", "url", "uri", "path", "document_id")


def _payload_summary(payload_json: str | None) -> str:
    if not payload_json:
        return ""
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in _SUMMARY_KEYS:
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return None


def _seconds_since(start: str | None, end: datetime) -> float | None:
    parsed = _parse_iso(start)
    if parsed is None:
        return None
    return (end - parsed).total_seconds()


def report(
    conn: sqlite3.Connection,
    *,
    content_type: str | None = None,
    recent_limit: int = 5,
) -> dict[str, Any]:
    """Return an embedding-pipeline progress snapshot."""
    recent_limit = max(0, min(int(recent_limit), 20))
    now = datetime.now(UTC)

    ct_filter = " WHERE content_type = ?" if content_type else ""
    ct_params: list[Any] = [content_type] if content_type else []

    total = conn.execute(
        f"SELECT COUNT(*) FROM documents{ct_filter}", ct_params
    ).fetchone()[0]

    status_rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM documents{ct_filter} GROUP BY status",
        ct_params,
    ).fetchall()
    by_status: dict[str, int] = {row["status"]: row["n"] for row in status_rows}

    ct_rows = conn.execute(
        f"""
        SELECT content_type, status, COUNT(*) AS n
          FROM documents{ct_filter}
         GROUP BY content_type, status
        """,
        ct_params,
    ).fetchall()
    by_content_type: dict[str, dict[str, int]] = {}
    for row in ct_rows:
        by_content_type.setdefault(row["content_type"], {})[row["status"]] = row["n"]

    pending_ct_clause = " AND content_type = ?" if content_type else ""
    oldest_row = conn.execute(
        f"""
        SELECT id, title, content_type, created_at
          FROM documents
         WHERE status = 'pending'{pending_ct_clause}
         ORDER BY created_at ASC
         LIMIT 1
        """,
        ct_params,
    ).fetchone()
    oldest_pending: dict[str, Any] | None = None
    if oldest_row is not None:
        age = _seconds_since(oldest_row["created_at"], now)
        oldest_pending = {
            "id": oldest_row["id"],
            "title": oldest_row["title"],
            "content_type": oldest_row["content_type"],
            "created_at": oldest_row["created_at"],
            "age_minutes": round(age / 60, 1) if age is not None else None,
        }

    inflight_rows = conn.execute(
        """
        SELECT id, kind, payload, started_at
          FROM job_queue
         WHERE status = 'running'
           AND kind LIKE 'ingest_%'
         ORDER BY started_at ASC
        """
    ).fetchall()
    in_flight: list[dict[str, Any]] = []
    for row in inflight_rows:
        running_for = _seconds_since(row["started_at"], now)
        in_flight.append(
            {
                "job_id": row["id"],
                "kind": row["kind"],
                "summary": _payload_summary(row["payload"]),
                "started_at": row["started_at"],
                "running_for_seconds": round(running_for, 1) if running_for is not None else None,
            }
        )

    recent_rows = conn.execute(
        """
        SELECT id, kind, status, payload, started_at, completed_at
          FROM job_queue
         WHERE kind LIKE 'ingest_%'
           AND status IN ('complete', 'failed', 'cancelled')
           AND completed_at IS NOT NULL
         ORDER BY completed_at DESC
         LIMIT ?
        """,
        (recent_limit,),
    ).fetchall()
    recently_completed: list[dict[str, Any]] = []
    for row in recent_rows:
        started = _parse_iso(row["started_at"])
        ended = _parse_iso(row["completed_at"])
        duration = round((ended - started).total_seconds(), 1) if started and ended else None
        recently_completed.append(
            {
                "job_id": row["id"],
                "kind": row["kind"],
                "status": row["status"],
                "summary": _payload_summary(row["payload"]),
                "completed_at": row["completed_at"],
                "duration_seconds": duration,
            }
        )

    jobs_last_hour = conn.execute(
        """
        SELECT COUNT(*) FROM job_queue
         WHERE kind LIKE 'ingest_%'
           AND status = 'complete'
           AND completed_at >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 hour')
        """
    ).fetchone()[0]

    docs_last_hour = conn.execute(
        f"""
        SELECT COUNT(*) FROM documents
         WHERE status = 'embedded'
           AND updated_at >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 hour')
           {pending_ct_clause}
        """,
        ct_params,
    ).fetchone()[0]

    return {
        "total": total,
        "by_status": by_status,
        "by_content_type": by_content_type,
        "oldest_pending": oldest_pending,
        "in_flight": in_flight,
        "recently_completed": recently_completed,
        "recent_throughput": {
            "window": "last_1h",
            "ingest_jobs_completed": jobs_last_hour,
            "documents_embedded": docs_last_hour,
        },
    }
