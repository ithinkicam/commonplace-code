"""Unit tests for commonplace_worker.handlers.book_enrichment."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from unittest.mock import patch

import pytest

from commonplace_db.db import connect, migrate
from commonplace_worker.handlers.book_enrichment import (
    ELIGIBLE_CONTENT_TYPES,
    ingest_book_enrichment,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> sqlite3.Connection:
    """In-memory DB with all migrations applied."""
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _insert_doc(
    conn: sqlite3.Connection,
    content_type: str = "storygraph_entry",
    title: str = "Test Book",
    author: str = "Test Author",
    enriched_at: str | None = None,
    description: str | None = None,
) -> int:
    """Insert a minimal document row and return its id."""
    with conn:
        cur = conn.execute(
            """
            INSERT INTO documents
                (content_type, title, author, content_hash, status,
                 enriched_at, description)
            VALUES (?, ?, ?, ?, 'complete', ?, ?)
            """,
            (
                content_type,
                title,
                author,
                f"hash_{title}_{author}_{content_type}",
                enriched_at,
                description,
            ),
        )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Fake API clients
# ---------------------------------------------------------------------------


class _FakeOLClient:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    def get_book_data(self, title: str, author: str | None) -> dict | None:
        return self._data


class _FakeGBClient:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    def get_book_data(self, title: str, author: str | None) -> dict | None:
        return self._data


_OL_SUCCESS = {
    "description": "A great book about things.",
    "subjects": ["Fiction", "Adventure"],
    "first_published_year": 2001,
    "isbn": "9780000000001",
    "source": "open_library",
}

_GB_SUCCESS = {
    "description": "Google Books description.",
    "subjects": ["Science"],
    "first_published_year": 1990,
    "isbn": "9780000000002",
    "source": "google_books",
}


# ---------------------------------------------------------------------------
# Happy path: OL has description
# ---------------------------------------------------------------------------


def test_enrich_from_open_library(db: sqlite3.Connection) -> None:
    """Handler enriches document from Open Library when OL has a description."""
    doc_id = _insert_doc(db, content_type="storygraph_entry")

    with (
        patch(
            "commonplace_worker.handlers.book_enrichment._try_open_library",
            return_value=_OL_SUCCESS,
        ),
        patch("commonplace_worker.handlers.book_enrichment._embed_description"),
    ):
        result = ingest_book_enrichment({"document_id": doc_id}, db)

    assert result["action"] == "enriched"
    assert result["source"] == "open_library"

    row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["description"] == "A great book about things."
    assert row["enrichment_source"] == "open_library"
    assert row["enriched_at"] is not None
    subjects = json.loads(row["subjects"])
    assert "Fiction" in subjects


# ---------------------------------------------------------------------------
# Fallback: OL has no description → Google Books
# ---------------------------------------------------------------------------


def test_fallback_to_google_books_when_ol_no_description(db: sqlite3.Connection) -> None:
    """Handler falls back to Google Books when OL returns no description."""
    doc_id = _insert_doc(db, content_type="audiobook")

    ol_no_desc = dict(_OL_SUCCESS, description=None)

    with (
        patch(
            "commonplace_worker.handlers.book_enrichment._try_open_library",
            return_value=ol_no_desc,
        ),
        patch(
            "commonplace_worker.handlers.book_enrichment._try_google_books",
            return_value=_GB_SUCCESS,
        ),
        patch("commonplace_worker.handlers.book_enrichment._embed_description"),
    ):
        result = ingest_book_enrichment({"document_id": doc_id}, db)

    assert result["action"] == "enriched"

    row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["description"] == "Google Books description."


# ---------------------------------------------------------------------------
# Both APIs fail → document left unenriched but no error raised
# ---------------------------------------------------------------------------


def test_both_apis_fail_leaves_unenriched(db: sqlite3.Connection) -> None:
    """When both OL and GB fail, document is left unenriched (no exception)."""
    doc_id = _insert_doc(db, content_type="book")

    with (
        patch(
            "commonplace_worker.handlers.book_enrichment._try_open_library",
            return_value=None,
        ),
        patch(
            "commonplace_worker.handlers.book_enrichment._try_google_books",
            return_value=None,
        ),
    ):
        result = ingest_book_enrichment({"document_id": doc_id}, db)

    assert result["action"] == "unenriched"

    row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["enriched_at"] is None
    assert row["description"] is None


# ---------------------------------------------------------------------------
# Idempotency: skip already-enriched
# ---------------------------------------------------------------------------


def test_idempotent_skip_already_enriched(db: sqlite3.Connection) -> None:
    """Handler skips documents that already have enriched_at and description."""
    doc_id = _insert_doc(
        db,
        content_type="kindle_book",
        enriched_at="2024-01-01T00:00:00Z",
        description="Existing description.",
    )

    with patch(
        "commonplace_worker.handlers.book_enrichment._try_open_library"
    ) as mock_ol:
        result = ingest_book_enrichment({"document_id": doc_id}, db)

    assert result["action"] == "skipped"
    mock_ol.assert_not_called()


# ---------------------------------------------------------------------------
# force=True overrides idempotency
# ---------------------------------------------------------------------------


def test_force_true_re_enriches(db: sqlite3.Connection) -> None:
    """force=True causes re-enrichment even if already enriched."""
    doc_id = _insert_doc(
        db,
        content_type="storygraph_entry",
        enriched_at="2024-01-01T00:00:00Z",
        description="Old description.",
    )

    new_data = dict(_OL_SUCCESS, description="New description.")

    with (
        patch(
            "commonplace_worker.handlers.book_enrichment._try_open_library",
            return_value=new_data,
        ),
        patch("commonplace_worker.handlers.book_enrichment._embed_description"),
    ):
        result = ingest_book_enrichment({"document_id": doc_id, "force": True}, db)

    assert result["action"] == "enriched"

    row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["description"] == "New description."


# ---------------------------------------------------------------------------
# Ineligible content_type → skipped
# ---------------------------------------------------------------------------


def test_ineligible_content_type_skipped(db: sqlite3.Connection) -> None:
    """Handler skips documents with non-book content_type."""
    doc_id = _insert_doc(db, content_type="bluesky_post")

    with patch(
        "commonplace_worker.handlers.book_enrichment._try_open_library"
    ) as mock_ol:
        result = ingest_book_enrichment({"document_id": doc_id}, db)

    assert result["action"] == "skipped"
    mock_ol.assert_not_called()


# ---------------------------------------------------------------------------
# Missing document_id in payload → ValueError
# ---------------------------------------------------------------------------


def test_missing_document_id_raises(db: sqlite3.Connection) -> None:
    """Handler raises ValueError if document_id missing from payload."""
    with pytest.raises(ValueError, match="missing 'document_id'"):
        ingest_book_enrichment({}, db)


# ---------------------------------------------------------------------------
# Document not found → ValueError
# ---------------------------------------------------------------------------


def test_document_not_found_raises(db: sqlite3.Connection) -> None:
    """Handler raises ValueError if document_id not in DB."""
    with pytest.raises(ValueError, match="document not found"):
        ingest_book_enrichment({"document_id": 99999}, db)


# ---------------------------------------------------------------------------
# Subjects serialised as JSON array
# ---------------------------------------------------------------------------


def test_subjects_serialised_as_json_array(db: sqlite3.Connection) -> None:
    """Subjects stored as a JSON array string, parseable back to list."""
    doc_id = _insert_doc(db, content_type="book")
    subjects = ["Science Fiction", "Space Opera", "Classic"]
    data = dict(_OL_SUCCESS, subjects=subjects)

    with (
        patch(
            "commonplace_worker.handlers.book_enrichment._try_open_library",
            return_value=data,
        ),
        patch("commonplace_worker.handlers.book_enrichment._embed_description"),
    ):
        ingest_book_enrichment({"document_id": doc_id}, db)

    row = db.execute("SELECT subjects FROM documents WHERE id = ?", (doc_id,)).fetchone()
    parsed = json.loads(row["subjects"])
    assert parsed == subjects


# ---------------------------------------------------------------------------
# ISBN extraction
# ---------------------------------------------------------------------------


def test_isbn_written_to_db(db: sqlite3.Connection) -> None:
    """Handler writes ISBN to documents row."""
    doc_id = _insert_doc(db, content_type="audiobook")

    with (
        patch(
            "commonplace_worker.handlers.book_enrichment._try_open_library",
            return_value=_OL_SUCCESS,
        ),
        patch("commonplace_worker.handlers.book_enrichment._embed_description"),
    ):
        ingest_book_enrichment({"document_id": doc_id}, db)

    row = db.execute("SELECT isbn FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["isbn"] == "9780000000001"


# ---------------------------------------------------------------------------
# No title → skipped gracefully
# ---------------------------------------------------------------------------


def test_no_title_skipped(db: sqlite3.Connection) -> None:
    """Handler skips gracefully when document has no title."""
    with db:
        cur = db.execute(
            "INSERT INTO documents (content_type, title, content_hash, status) "
            "VALUES ('storygraph_entry', NULL, 'hash_notitle', 'complete')"
        )
    doc_id = cur.lastrowid

    with patch(
        "commonplace_worker.handlers.book_enrichment._try_open_library"
    ) as mock_ol:
        result = ingest_book_enrichment({"document_id": doc_id}, db)

    assert result["action"] == "skipped"
    mock_ol.assert_not_called()


# ---------------------------------------------------------------------------
# All eligible content types are handled
# ---------------------------------------------------------------------------


def test_all_eligible_content_types_are_processed(db: sqlite3.Connection) -> None:
    """Each eligible content_type gets the 'enriched' action."""
    for ct in ELIGIBLE_CONTENT_TYPES:
        doc_id = _insert_doc(db, content_type=ct, title=f"Book for {ct}")

        with (
            patch(
                "commonplace_worker.handlers.book_enrichment._try_open_library",
                return_value=_OL_SUCCESS,
            ),
            patch("commonplace_worker.handlers.book_enrichment._embed_description"),
        ):
            result = ingest_book_enrichment({"document_id": doc_id}, db)

        assert result["action"] == "enriched", f"Expected 'enriched' for content_type={ct}"
