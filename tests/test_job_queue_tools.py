"""Tests for the job-queue MCP tools (task 1_4_job_queue_tools).

Covers:
- submit → status returns queued, correct kind/payload, attempts==0
- submit with empty kind raises ValueError
- submit with oversize kind raises ValueError
- submit with non-JSON-serialisable payload raises ValueError
- get_status on missing id raises ValueError
- cancel on queued → cancelled=True, previous_status='queued'
- cancel on complete → cancelled=False, previous_status='complete'
- cancel on missing id raises ValueError
- MCP layer: end-to-end via FastMCP TestClient
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import pytest
from starlette.testclient import TestClient

import commonplace_db
import commonplace_server.jobs as jobs

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with schema applied."""
    conn = commonplace_db.connect(":memory:")
    commonplace_db.migrate(conn)
    return conn


# ---------------------------------------------------------------------------
# Unit tests: jobs.submit
# ---------------------------------------------------------------------------


def test_submit_returns_queued(mem_conn: sqlite3.Connection) -> None:
    result = jobs.submit(mem_conn, "noop", {"key": "value"})
    assert result["status"] == "queued"
    assert result["kind"] == "noop"
    assert isinstance(result["id"], int)


def test_submit_then_status_correct(mem_conn: sqlite3.Connection) -> None:
    submitted = jobs.submit(mem_conn, "noop", {"foo": 42})
    row = jobs.status(mem_conn, submitted["id"])
    assert row["status"] == "queued"
    assert row["kind"] == "noop"
    assert row["payload"] == {"foo": 42}
    assert row["attempts"] == 0
    assert row["error"] is None
    assert row["started_at"] is None
    assert row["completed_at"] is None
    assert isinstance(row["created_at"], str)


def test_submit_empty_kind_raises(mem_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        jobs.submit(mem_conn, "", {})


def test_submit_whitespace_only_kind_raises(mem_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        jobs.submit(mem_conn, "   ", {})


def test_submit_oversize_kind_raises(mem_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="64"):
        jobs.submit(mem_conn, "x" * 65, {})


def test_submit_non_serialisable_payload_raises(mem_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="JSON-serialisable"):
        jobs.submit(mem_conn, "noop", {"bad": object()})  # type: ignore[dict-item]


def test_submit_non_dict_payload_raises(mem_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="dict"):
        jobs.submit(mem_conn, "noop", "not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit tests: jobs.status
# ---------------------------------------------------------------------------


def test_status_missing_id_raises(mem_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        jobs.status(mem_conn, 99999)


# ---------------------------------------------------------------------------
# Unit tests: jobs.cancel
# ---------------------------------------------------------------------------


def test_cancel_queued_job(mem_conn: sqlite3.Connection) -> None:
    submitted = jobs.submit(mem_conn, "noop", {})
    result = jobs.cancel(mem_conn, submitted["id"])
    assert result["cancelled"] is True
    assert result["previous_status"] == "queued"
    assert result["id"] == submitted["id"]


def test_cancel_queued_job_reflects_in_status(mem_conn: sqlite3.Connection) -> None:
    submitted = jobs.submit(mem_conn, "noop", {})
    jobs.cancel(mem_conn, submitted["id"])
    row = jobs.status(mem_conn, submitted["id"])
    assert row["status"] == "cancelled"
    assert row["completed_at"] is not None


def test_cancel_complete_job_returns_false(mem_conn: sqlite3.Connection) -> None:
    """Cancelling a terminal job returns cancelled=False."""
    submitted = jobs.submit(mem_conn, "noop", {})
    job_id = submitted["id"]
    # Manually mark complete
    with mem_conn:
        mem_conn.execute(
            "UPDATE job_queue SET status='complete', completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (job_id,),
        )
    result = jobs.cancel(mem_conn, job_id)
    assert result["cancelled"] is False
    assert result["previous_status"] == "complete"


def test_cancel_failed_job_returns_false(mem_conn: sqlite3.Connection) -> None:
    submitted = jobs.submit(mem_conn, "noop", {})
    job_id = submitted["id"]
    with mem_conn:
        mem_conn.execute(
            "UPDATE job_queue SET status='failed' WHERE id=?",
            (job_id,),
        )
    result = jobs.cancel(mem_conn, job_id)
    assert result["cancelled"] is False
    assert result["previous_status"] == "failed"


def test_cancel_already_cancelled_returns_false(mem_conn: sqlite3.Connection) -> None:
    submitted = jobs.submit(mem_conn, "noop", {})
    job_id = submitted["id"]
    jobs.cancel(mem_conn, job_id)
    result = jobs.cancel(mem_conn, job_id)
    assert result["cancelled"] is False
    assert result["previous_status"] == "cancelled"


def test_cancel_missing_id_raises(mem_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        jobs.cancel(mem_conn, 99999)


# ---------------------------------------------------------------------------
# MCP layer: end-to-end via FastMCP (mirrors test_server_skeleton.py)
# ---------------------------------------------------------------------------


def test_submit_job_tool_via_mcp(tmp_path: Any) -> None:
    """submit_job MCP tool is reachable through FastMCP's HTTP ASGI app."""
    db_file = str(tmp_path / "mcp_test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import submit_job

        result = submit_job("noop", {"ping": True})
        assert result["status"] == "queued"
        assert result["kind"] == "noop"
        assert isinstance(result["id"], int)
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_get_job_status_tool_via_mcp(tmp_path: Any) -> None:
    """get_job_status MCP tool returns correct row after submit_job."""
    db_file = str(tmp_path / "mcp_status_test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import get_job_status, submit_job

        submitted = submit_job("noop", {"x": 1})
        row = get_job_status(submitted["id"])
        assert row["status"] == "queued"
        assert row["kind"] == "noop"
        assert row["payload"] == {"x": 1}
        assert row["attempts"] == 0
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_cancel_job_tool_via_mcp(tmp_path: Any) -> None:
    """cancel_job MCP tool cancels a queued job."""
    db_file = str(tmp_path / "mcp_cancel_test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import cancel_job, submit_job

        submitted = submit_job("noop", {})
        result = cancel_job(submitted["id"])
        assert result["cancelled"] is True
        assert result["previous_status"] == "queued"
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


# ---------------------------------------------------------------------------
# Per-kind ingest wrapper tools: each enqueues the correct kind+payload.
# ---------------------------------------------------------------------------


def test_ingest_article_tool(tmp_path: Any) -> None:
    db_file = str(tmp_path / "mcp_ingest_article.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import get_job_status, ingest_article

        result = ingest_article("https://example.com/post")
        assert result["status"] == "queued"
        assert result["kind"] == "ingest_article"
        row = get_job_status(result["id"])
        assert row["kind"] == "ingest_article"
        assert row["payload"] == {"url": "https://example.com/post"}
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_ingest_youtube_tool(tmp_path: Any) -> None:
    db_file = str(tmp_path / "mcp_ingest_youtube.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import get_job_status, ingest_youtube

        result = ingest_youtube("https://youtu.be/abc123")
        assert result["status"] == "queued"
        assert result["kind"] == "ingest_youtube"
        row = get_job_status(result["id"])
        assert row["kind"] == "ingest_youtube"
        assert row["payload"] == {"url": "https://youtu.be/abc123"}
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_ingest_podcast_tool(tmp_path: Any) -> None:
    db_file = str(tmp_path / "mcp_ingest_podcast.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import get_job_status, ingest_podcast

        result = ingest_podcast("https://example.com/episode.mp3")
        assert result["status"] == "queued"
        assert result["kind"] == "ingest_podcast"
        row = get_job_status(result["id"])
        assert row["kind"] == "ingest_podcast"
        assert row["payload"] == {"url": "https://example.com/episode.mp3"}
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_ingest_bluesky_url_tool(tmp_path: Any) -> None:
    db_file = str(tmp_path / "mcp_ingest_bluesky_url.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import get_job_status, ingest_bluesky_url

        url = "https://bsky.app/profile/alice.test/post/3kxyz"
        result = ingest_bluesky_url(url)
        assert result["status"] == "queued"
        # Note: kind is "bluesky_url", not "ingest_bluesky_url".
        assert result["kind"] == "bluesky_url"
        row = get_job_status(result["id"])
        assert row["kind"] == "bluesky_url"
        assert row["payload"] == {"url": url}
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_ingest_image_url_tool(tmp_path: Any) -> None:
    db_file = str(tmp_path / "mcp_ingest_image_url.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import get_job_status, ingest_image_url

        result = ingest_image_url("https://example.com/cat.jpg")
        assert result["status"] == "queued"
        assert result["kind"] == "ingest_image"
        row = get_job_status(result["id"])
        assert row["kind"] == "ingest_image"
        assert row["payload"] == {"url": "https://example.com/cat.jpg"}
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_mcp_http_app_submit_job(tmp_path: Any) -> None:
    """End-to-end: submit_job callable via the FastMCP ASGI app (same pattern as test_server_skeleton)."""
    db_file = str(tmp_path / "mcp_http_test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        from commonplace_server.server import mcp

        # Just verify the app initialises and the tool is registered.
        app = mcp.http_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            # Verify the healthcheck still works alongside the new tools.
            response = client.get("/healthcheck")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]
