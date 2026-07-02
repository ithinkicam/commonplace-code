from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from commonplace_db import connect, migrate
from commonplace_worker.therapy_watcher import run_watch


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


class FakeWatcherClient:
    def list_child_pages(self, parent_page_id: str) -> list[dict[str, Any]]:
        assert parent_page_id == "parent"
        return [
            {"id": "new-page", "type": "child_page"},
            {"id": "changed-page", "type": "child_page"},
            {"id": "unchanged-page", "type": "child_page"},
        ]

    def get_page(self, page_id: str) -> dict[str, Any]:
        titles = {
            "new-page": "May 1, 2026",
            "changed-page": "May 8, 2026",
            "unchanged-page": "May 15, 2026",
        }
        edited = {
            "new-page": "2026-05-01T10:00:00.000Z",
            "changed-page": "2026-05-09T10:00:00.000Z",
            "unchanged-page": "2026-05-15T10:00:00.000Z",
        }
        return {
            "id": page_id,
            "url": f"https://notion.so/{page_id}",
            "last_edited_time": edited[page_id],
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [
                        {
                            "plain_text": titles[page_id],
                            "annotations": {},
                        }
                    ],
                }
            },
        }


def _seed_existing(db: sqlite3.Connection) -> None:
    db.execute(
        """
        INSERT INTO documents (content_type, source_id, title, status)
        VALUES ('therapy_session', 'changed-page', 'May 8, 2026', 'embedded')
        """
    )
    changed_doc = db.execute(
        "SELECT id FROM documents WHERE source_id='changed-page'"
    ).fetchone()["id"]
    db.execute(
        """
        INSERT INTO therapy_session_meta
            (document_id, session_date, therapist, session_type,
             notion_page_id, notion_url, notion_last_edited_at)
        VALUES (?, '2026-05-08', 'Christina', 'individual',
                'changed-page', 'https://notion.so/changed-page',
                '2026-05-08T10:00:00.000Z')
        """,
        (changed_doc,),
    )
    db.execute(
        """
        INSERT INTO documents (content_type, source_id, title, status)
        VALUES ('therapy_session', 'unchanged-page', 'May 15, 2026', 'embedded')
        """
    )
    unchanged_doc = db.execute(
        "SELECT id FROM documents WHERE source_id='unchanged-page'"
    ).fetchone()["id"]
    db.execute(
        """
        INSERT INTO therapy_session_meta
            (document_id, session_date, therapist, session_type,
             notion_page_id, notion_url, notion_last_edited_at)
        VALUES (?, '2026-05-15', 'Christina', 'individual',
                'unchanged-page', 'https://notion.so/unchanged-page',
                '2026-05-15T10:00:00.000Z')
        """,
        (unchanged_doc,),
    )
    db.commit()


def test_watcher_enqueues_new_and_changed_pages(db: sqlite3.Connection) -> None:
    _seed_existing(db)
    result = run_watch(db, parent_page_id="parent", _client=FakeWatcherClient())

    assert result.pages_found == 3
    assert result.enqueued == 2
    assert result.skipped == 1

    jobs = db.execute(
        "SELECT kind, payload FROM job_queue WHERE kind='ingest_therapy_session' ORDER BY id"
    ).fetchall()
    assert len(jobs) == 2
    payloads = [json.loads(row["payload"]) for row in jobs]
    assert payloads == [
        {"notion_page_id": "new-page"},
        {"notion_page_id": "changed-page"},
    ]

    run = db.execute(
        "SELECT name, status, details FROM scheduled_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["name"] == "notion_therapy_watcher"
    assert run["status"] == "success"
    assert json.loads(run["details"])["enqueued"] == 2


def test_watcher_dry_run_does_not_enqueue(db: sqlite3.Connection) -> None:
    _seed_existing(db)
    result = run_watch(
        db,
        parent_page_id="parent",
        dry_run=True,
        _client=FakeWatcherClient(),
    )

    assert result.enqueued == 2
    assert db.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0] == 0


def test_healthcheck_reports_last_watcher_run(db: sqlite3.Connection) -> None:
    run_watch(db, parent_page_id="parent", dry_run=True, _client=FakeWatcherClient())

    from commonplace_server.server import _build_health_payload

    payload = _build_health_payload(db)
    signals = payload["signals"]
    assert "notion_therapy_watcher_last_successful_run_at" in signals
    assert "notion_therapy_watcher_last_details" in signals
