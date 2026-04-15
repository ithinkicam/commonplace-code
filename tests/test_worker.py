"""Tests for commonplace_worker.worker."""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from commonplace_db import migrate
from commonplace_worker.worker import HANDLERS, Handler, poll_once, run_forever

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    """Return a migrated in-memory SQLite connection."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def _enqueue(conn: sqlite3.Connection, kind: str, payload: str = "{}") -> int:
    """Insert a queued job and return its id."""
    with conn:
        cur = conn.execute(
            "INSERT INTO job_queue (kind, payload, status) VALUES (?, ?, 'queued') RETURNING id",
            (kind, payload),
        )
        row = cur.fetchone()
    return int(row["id"])


def _fetch_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM job_queue WHERE id = ?", (job_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_poll_once_empty_queue_returns_zero() -> None:
    """poll_once on an empty queue returns 0 without sleeping."""
    conn = _make_conn()
    result = poll_once(conn, HANDLERS)
    assert result == 0


def test_poll_once_noop_handler_marks_complete() -> None:
    """Enqueue a 'noop' job; poll_once should mark it complete."""
    conn = _make_conn()
    job_id = _enqueue(conn, "noop")

    result = poll_once(conn, HANDLERS)
    assert result == 1

    row = _fetch_job(conn, job_id)
    assert row["status"] == "complete"
    assert row["completed_at"] is not None
    assert row["error"] is None
    assert row["attempts"] == 1


def test_poll_once_failing_handler_marks_failed() -> None:
    """Enqueue a job whose handler raises; row should be failed with error populated."""
    conn = _make_conn()

    def _boom(_payload: dict[str, Any]) -> None:
        raise RuntimeError("intentional failure")

    handlers: dict[str, Handler] = {"bomb": _boom}
    job_id = _enqueue(conn, "bomb")

    result = poll_once(conn, handlers)
    assert result == 1

    row = _fetch_job(conn, job_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert row["error"] is not None
    assert "intentional failure" in row["error"]
    assert row["completed_at"] is not None


def test_poll_once_unknown_kind_marks_failed() -> None:
    """Enqueue a job with an unregistered kind; should fail with a clear error."""
    conn = _make_conn()
    job_id = _enqueue(conn, "does_not_exist")

    result = poll_once(conn, HANDLERS)
    assert result == 1

    row = _fetch_job(conn, job_id)
    assert row["status"] == "failed"
    assert row["error"] is not None
    assert "does_not_exist" in row["error"]


def test_poll_once_cancelled_row_is_ignored() -> None:
    """A row with status='cancelled' must not be claimed or processed."""
    conn = _make_conn()
    job_id = _enqueue(conn, "noop")
    # Manually flip to cancelled before the worker runs.
    with conn:
        conn.execute(
            "UPDATE job_queue SET status='cancelled' WHERE id=?", (job_id,)
        )

    result = poll_once(conn, HANDLERS)
    assert result == 0

    row = _fetch_job(conn, job_id)
    assert row["status"] == "cancelled"


def test_run_forever_stops_on_event() -> None:
    """run_forever should exit cleanly when stop_event is set."""
    conn = _make_conn()
    stop_event = threading.Event()

    thread = threading.Thread(
        target=run_forever,
        kwargs={
            "conn": conn,
            "handlers": HANDLERS,
            "idle_sleep": 0.05,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    thread.start()

    # Give it a moment to enter the loop, then signal stop.
    time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "run_forever did not stop within timeout"


def test_poll_once_processes_jobs_in_order() -> None:
    """Jobs should be processed in created_at order (FIFO)."""
    conn = _make_conn()
    processed_order: list[str] = []

    def _record(payload: dict[str, Any]) -> None:
        processed_order.append(payload["name"])

    handlers: dict[str, Handler] = {"ordered": _record}

    _enqueue(conn, "ordered", '{"name": "first"}')
    # Small sleep to ensure different created_at timestamps.
    time.sleep(0.01)
    _enqueue(conn, "ordered", '{"name": "second"}')

    poll_once(conn, handlers)
    poll_once(conn, handlers)

    assert processed_order == ["first", "second"]


def test_attempts_increments_on_failure() -> None:
    """attempts column must increment even on failure."""
    conn = _make_conn()

    def _fail(_payload: dict[str, Any]) -> None:
        raise ValueError("oops")

    handlers: dict[str, Handler] = {"fail_kind": _fail}
    job_id = _enqueue(conn, "fail_kind")

    poll_once(conn, handlers)
    row = _fetch_job(conn, job_id)
    assert row["attempts"] == 1


def test_noop_handler_exists_in_registry() -> None:
    """The built-in HANDLERS dict must include a 'noop' entry."""
    assert "noop" in HANDLERS
    # Call it to confirm it accepts a dict and returns None.
    result = HANDLERS["noop"]({"any": "payload"})
    assert result is None
