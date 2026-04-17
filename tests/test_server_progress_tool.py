"""Tests for the `embedding_progress` MCP tool and its pure report helper."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import commonplace_db
from commonplace_server import progress

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_conn(tmp_path: Path) -> sqlite3.Connection:
    db_file = tmp_path / "progress.db"
    conn = commonplace_db.connect(str(db_file))
    commonplace_db.migrate(conn)
    return conn


def _insert_document(
    conn: sqlite3.Connection,
    *,
    content_type: str,
    title: str,
    status: str = "pending",
    created_at: str = "2026-04-17T10:00:00Z",
    updated_at: str | None = None,
    content_hash: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO documents (content_type, title, content_hash, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            content_type,
            title,
            content_hash or f"hash-{title}",
            status,
            created_at,
            updated_at or created_at,
        ),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_job(
    conn: sqlite3.Connection,
    *,
    kind: str,
    status: str,
    payload: dict[str, Any],
    created_at: str = "2026-04-17T10:00:00Z",
    started_at: str | None = None,
    completed_at: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO job_queue (kind, payload, status, created_at, started_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (kind, json.dumps(payload), status, created_at, started_at, completed_at),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_embedding_progress_tool_is_registered() -> None:
    from commonplace_server.server import embedding_progress, mcp

    assert callable(embedding_progress)
    tool_names = set(mcp._tool_manager._tools.keys())
    assert "embedding_progress" in tool_names, tool_names


# ---------------------------------------------------------------------------
# Report shape — empty DB
# ---------------------------------------------------------------------------


def test_report_empty_db(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        result = progress.report(conn)
    finally:
        conn.close()

    assert result["total"] == 0
    assert result["by_status"] == {}
    assert result["by_content_type"] == {}
    assert result["oldest_pending"] is None
    assert result["in_flight"] == []
    assert result["recently_completed"] == []
    assert result["recent_throughput"]["ingest_jobs_completed"] == 0
    assert result["recent_throughput"]["documents_embedded"] == 0


# ---------------------------------------------------------------------------
# Report shape — populated
# ---------------------------------------------------------------------------


def test_report_counts_and_oldest_pending(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_document(
            conn,
            content_type="book",
            title="Old Pending",
            status="pending",
            created_at="2026-04-01T00:00:00Z",
        )
        _insert_document(
            conn,
            content_type="book",
            title="New Pending",
            status="pending",
            created_at="2026-04-17T00:00:00Z",
        )
        _insert_document(conn, content_type="book", title="Done A", status="embedded")
        _insert_document(conn, content_type="article", title="Done B", status="embedded")
        _insert_document(conn, content_type="article", title="Bad", status="failed")

        result = progress.report(conn)
    finally:
        conn.close()

    assert result["total"] == 5
    assert result["by_status"] == {"pending": 2, "embedded": 2, "failed": 1}
    assert result["by_content_type"]["book"] == {"pending": 2, "embedded": 1}
    assert result["by_content_type"]["article"] == {"embedded": 1, "failed": 1}
    assert result["oldest_pending"]["title"] == "Old Pending"
    assert result["oldest_pending"]["content_type"] == "book"
    assert result["oldest_pending"]["age_minutes"] is not None


def test_report_content_type_filter(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_document(conn, content_type="book", title="B1", status="pending")
        _insert_document(conn, content_type="article", title="A1", status="pending")
        _insert_document(conn, content_type="article", title="A2", status="embedded")

        result = progress.report(conn, content_type="article")
    finally:
        conn.close()

    assert result["total"] == 2
    assert result["by_status"] == {"pending": 1, "embedded": 1}
    assert set(result["by_content_type"].keys()) == {"article"}
    assert result["oldest_pending"]["title"] == "A1"


def test_report_in_flight_and_recently_completed(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        # A non-ingest running job should be ignored.
        _insert_job(
            conn,
            kind="generate_book_note",
            status="running",
            payload={"document_id": 99},
            started_at="2026-04-17T10:00:00Z",
        )
        # Two in-flight ingest jobs, various payload shapes.
        _insert_job(
            conn,
            kind="ingest_article",
            status="running",
            payload={"url": "https://example.com/a"},
            started_at="2026-04-17T10:00:00Z",
        )
        _insert_job(
            conn,
            kind="ingest_book_enrichment",
            status="running",
            payload={"document_id": 42},
            started_at="2026-04-17T10:01:00Z",
        )
        # Recently completed — newest first expected.
        _insert_job(
            conn,
            kind="ingest_article",
            status="complete",
            payload={"title": "Old done"},
            started_at="2026-04-17T09:55:00Z",
            completed_at="2026-04-17T09:56:00Z",
        )
        _insert_job(
            conn,
            kind="ingest_video",
            status="failed",
            payload={"path": "/tmp/clip.mp4"},
            started_at="2026-04-17T09:58:00Z",
            completed_at="2026-04-17T09:59:30Z",
        )

        result = progress.report(conn, recent_limit=10)
    finally:
        conn.close()

    kinds_in_flight = [j["kind"] for j in result["in_flight"]]
    assert kinds_in_flight == ["ingest_article", "ingest_book_enrichment"]
    summaries = [j["summary"] for j in result["in_flight"]]
    assert summaries == ["https://example.com/a", "42"]
    for job in result["in_flight"]:
        assert job["running_for_seconds"] is not None

    recent = result["recently_completed"]
    assert [j["status"] for j in recent] == ["failed", "complete"]
    assert recent[0]["summary"] == "/tmp/clip.mp4"
    assert recent[0]["duration_seconds"] == 90.0
    assert recent[1]["summary"] == "Old done"


def test_report_recent_limit_clamp(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        result = progress.report(conn, recent_limit=-5)
        assert result["recently_completed"] == []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool passthrough
# ---------------------------------------------------------------------------


def test_embedding_progress_tool_returns_report(tmp_path: Path) -> None:
    db_file = str(tmp_path / "tool.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        # Seed one document via a fresh connection.
        conn = commonplace_db.connect(db_file)
        commonplace_db.migrate(conn)
        _insert_document(conn, content_type="book", title="Seed", status="embedded")
        conn.close()

        from commonplace_server.server import embedding_progress

        result = embedding_progress()
        assert result["total"] == 1
        assert result["by_status"] == {"embedded": 1}
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]
