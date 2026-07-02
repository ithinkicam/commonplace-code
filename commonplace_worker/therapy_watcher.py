"""Scheduled Notion watcher for Therapy session pages."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from commonplace_server.jobs import submit
from commonplace_worker.notion import (
    NotionClient,
    page_summary,
    resolve_therapy_parent_page_id,
)

logger = logging.getLogger(__name__)

WATCHER_NAME = "notion_therapy_watcher"


@dataclass(frozen=True)
class WatchResult:
    pages_found: int
    enqueued: int
    skipped: int
    skipped_in_flight: int
    dry_run: bool
    elapsed_ms: float


def run_watch(
    conn: sqlite3.Connection,
    *,
    parent_page_id: str | None = None,
    dry_run: bool = False,
    limit_pages: int | None = None,
    _client: Any = None,
) -> WatchResult:
    """List Therapy child pages and enqueue changed sessions."""
    t0 = time.monotonic()
    client = _client if _client is not None else NotionClient()
    parent_id = parent_page_id or resolve_therapy_parent_page_id()

    enqueued = 0
    skipped = 0
    skipped_in_flight = 0
    pages = client.list_child_pages(parent_id)
    if limit_pages is not None:
        pages = pages[:limit_pages]

    for child in pages:
        page_id = child.get("id")
        if not isinstance(page_id, str) or not page_id:
            skipped += 1
            continue
        page = client.get_page(page_id)
        summary = page_summary(page)
        stored = _stored_last_edited(conn, summary.page_id)
        changed = stored is None or summary.last_edited_time > stored
        if not changed:
            skipped += 1
            continue
        if _has_in_flight_job(conn, summary.page_id):
            skipped_in_flight += 1
            continue
        if dry_run:
            enqueued += 1
            continue
        submit(conn, "ingest_therapy_session", {"notion_page_id": summary.page_id})
        enqueued += 1

    elapsed_ms = (time.monotonic() - t0) * 1000
    result = WatchResult(
        pages_found=len(pages),
        enqueued=enqueued,
        skipped=skipped,
        skipped_in_flight=skipped_in_flight,
        dry_run=dry_run,
        elapsed_ms=elapsed_ms,
    )
    _record_run(conn, result)
    logger.info(
        "notion therapy watcher complete pages_found=%d enqueued=%d skipped=%d "
        "skipped_in_flight=%d dry_run=%s elapsed_ms=%.0f",
        result.pages_found,
        result.enqueued,
        result.skipped,
        result.skipped_in_flight,
        result.dry_run,
        result.elapsed_ms,
    )
    return result


def _stored_last_edited(conn: sqlite3.Connection, notion_page_id: str) -> str | None:
    row = conn.execute(
        "SELECT notion_last_edited_at FROM therapy_session_meta WHERE notion_page_id = ?",
        (notion_page_id,),
    ).fetchone()
    return str(row["notion_last_edited_at"]) if row is not None else None


def _has_in_flight_job(conn: sqlite3.Connection, notion_page_id: str) -> bool:
    payload = json.dumps({"notion_page_id": notion_page_id})
    row = conn.execute(
        """
        SELECT 1
          FROM job_queue
         WHERE kind = 'ingest_therapy_session'
           AND status IN ('queued', 'running')
           AND payload = ?
         LIMIT 1
        """,
        (payload,),
    ).fetchone()
    return row is not None


def _record_run(conn: sqlite3.Connection, result: WatchResult) -> None:
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    details = json.dumps(
        {
            "pages_found": result.pages_found,
            "enqueued": result.enqueued,
            "skipped": result.skipped,
            "skipped_in_flight": result.skipped_in_flight,
            "dry_run": result.dry_run,
            "elapsed_ms": result.elapsed_ms,
        },
        sort_keys=True,
    )
    with conn:
        conn.execute(
            """
            INSERT INTO scheduled_runs (name, status, details, started_at, completed_at)
            VALUES (?, 'success', ?, ?, ?)
            """,
            (WATCHER_NAME, details, now_iso, now_iso),
        )
