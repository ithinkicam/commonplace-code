"""Commonplace worker — job queue polling loop.

Public API
----------
Handler        type alias: Callable[[dict], None]
HANDLERS       registry: dict[str, Handler]
poll_once(conn, handlers) -> int
run_forever(conn, handlers, idle_sleep=1.0, stop_event=None)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Handler type and registry
# ---------------------------------------------------------------------------

Handler = Callable[[dict[str, Any]], None]


def _noop_handler(_payload: dict[str, Any]) -> None:
    """No-op handler used for round-trip testing."""


def _capture_handler(payload: dict[str, Any]) -> None:
    """Phase 1 stub: move an inbox file to the vault's captured folder.

    Real ingestion handlers (bluesky, youtube, article, etc.) are Phase 2 work.
    This handler only proves the pipeline: /capture → inbox → worker → vault.
    """
    inbox_file = payload.get("inbox_file")
    if not isinstance(inbox_file, str) or not inbox_file:
        raise ValueError(f"capture payload missing inbox_file: {payload!r}")

    inbox_dir = Path(
        os.environ.get("COMMONPLACE_INBOX_DIR", "~/commonplace-vault/inbox")
    ).expanduser()
    vault_dir = Path(
        os.environ.get("COMMONPLACE_VAULT_DIR", "~/commonplace-vault/captured")
    ).expanduser()

    src = inbox_dir / inbox_file
    if not src.exists():
        raise FileNotFoundError(f"inbox file not found: {src}")
    vault_dir.mkdir(parents=True, exist_ok=True)
    src.rename(vault_dir / inbox_file)


HANDLERS: dict[str, Handler] = {
    "noop": _noop_handler,
    "capture": _capture_handler,
}

# ---------------------------------------------------------------------------
# Job claiming and processing
# ---------------------------------------------------------------------------


def poll_once(conn: sqlite3.Connection, handlers: dict[str, Handler]) -> int:
    """Claim at most one queued job, run its handler, mark complete/failed.

    Returns the number of jobs processed (0 or 1).

    The claim is atomic: a single UPDATE ... RETURNING selects and marks
    'running' in one statement so concurrent workers cannot double-claim the
    same row (SQLite serializes writers, so the UPDATE is safe).
    """
    # Atomically claim the oldest queued row by updating its status to
    # 'running' and returning the row.  SQLite serializes all writers, so
    # two workers racing here cannot both claim the same row: the second
    # UPDATE simply finds no 'queued' row with that id and touches 0 rows.
    #
    # We use a nested SELECT in the WHERE rather than ORDER BY + LIMIT
    # directly in the UPDATE because SQLite only supports ORDER BY/LIMIT in
    # UPDATE when compiled with SQLITE_ENABLE_UPDATE_DELETE_LIMIT, which is
    # not guaranteed on macOS stock builds.
    with conn:
        row = conn.execute(
            """
            UPDATE job_queue
               SET status     = 'running',
                   started_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   attempts   = attempts + 1
             WHERE id = (
                     SELECT id FROM job_queue
                      WHERE status = 'queued'
                      ORDER BY created_at ASC
                      LIMIT 1
                   )
            RETURNING id, kind, payload, attempts
            """,
        ).fetchone()

    if row is None:
        return 0

    job_id: int = row["id"]
    kind: str = row["kind"]
    attempts: int = row["attempts"]
    try:
        payload: dict[str, Any] = json.loads(row["payload"])
    except json.JSONDecodeError as exc:
        _mark_failed(conn, job_id, attempts, f"Invalid JSON payload: {exc}")
        logger.error("job %d kind=%s failed (bad payload): %s", job_id, kind, exc)
        return 1

    start_ns = time.monotonic_ns()

    handler = handlers.get(kind)
    if handler is None:
        err = f"No handler registered for kind={kind!r}"
        _mark_failed(conn, job_id, attempts, err)
        elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        logger.error("job %d kind=%s failed in %dms: %s", job_id, kind, elapsed_ms, err)
        return 1

    try:
        handler(payload)
    except Exception as exc:  # noqa: BLE001
        err = repr(exc)
        _mark_failed(conn, job_id, attempts, err)
        elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        logger.error("job %d kind=%s failed in %dms: %s", job_id, kind, elapsed_ms, err)
        return 1

    elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
    _mark_complete(conn, job_id)
    logger.info("job %d kind=%s complete in %dms", job_id, kind, elapsed_ms)
    return 1


def _mark_complete(conn: sqlite3.Connection, job_id: int) -> None:
    with conn:
        conn.execute(
            """
            UPDATE job_queue
               SET status       = 'complete',
                   completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   error        = NULL
             WHERE id = ?
            """,
            (job_id,),
        )


def _mark_failed(conn: sqlite3.Connection, job_id: int, attempts: int, error: str) -> None:
    with conn:
        conn.execute(
            """
            UPDATE job_queue
               SET status       = 'failed',
                   completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   error        = ?,
                   attempts     = ?
             WHERE id = ?
            """,
            (error, attempts, job_id),
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_forever(
    conn: sqlite3.Connection,
    handlers: dict[str, Handler],
    idle_sleep: float = 1.0,
    stop_event: threading.Event | None = None,
) -> None:
    """Poll the job queue forever, sleeping when idle.

    Parameters
    ----------
    conn:
        Open SQLite connection returned by ``commonplace_db.connect()``.
    handlers:
        Handler registry mapping job kind → callable.
    idle_sleep:
        Seconds to sleep between polls when the queue is empty.
    stop_event:
        A ``threading.Event`` that, when set, causes the loop to exit cleanly
        after finishing any in-progress job.  SIGTERM and SIGINT also set this
        event.
    """
    import signal

    if stop_event is None:
        stop_event = threading.Event()

    # Signal handlers can only be registered from the main thread.
    # When run_forever is called from a test worker thread, skip registration.
    if threading.current_thread() is threading.main_thread():

        def _handle_signal(signum: int, _frame: object) -> None:
            logger.info("received signal %d — stopping worker", signum)
            stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

    logger.info("worker started — polling job queue")
    while not stop_event.is_set():
        processed = poll_once(conn, handlers)
        if processed == 0:
            # Nothing to do; sleep but wake early if stop_event fires.
            stop_event.wait(timeout=idle_sleep)
