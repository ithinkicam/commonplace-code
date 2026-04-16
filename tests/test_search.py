"""Tests for commonplace_server.search — semantic search across all content types."""

from __future__ import annotations

import sqlite3
import struct

import pytest

from commonplace_db import connect, migrate
from commonplace_server.search import results_to_dicts, search

_DIM = 768


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _make_vec(val: float) -> list[float]:
    """Return a 768-dim vector filled with *val*."""
    return [val] * _DIM


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _insert_doc(
    conn: sqlite3.Connection,
    content_type: str = "capture",
    title: str = "Test Doc",
    source_uri: str | None = None,
    source_id: str | None = None,
    created_at: str | None = None,
) -> int:
    """Insert a document and return its id."""
    if created_at:
        cur = conn.execute(
            "INSERT INTO documents (content_type, title, source_uri, source_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (content_type, title, source_uri, source_id, created_at),
        )
    else:
        cur = conn.execute(
            "INSERT INTO documents (content_type, title, source_uri, source_id) "
            "VALUES (?, ?, ?, ?)",
            (content_type, title, source_uri, source_id),
        )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _insert_chunk_with_embedding(
    conn: sqlite3.Connection,
    document_id: int,
    text: str,
    vec: list[float],
    chunk_index: int = 0,
) -> int:
    """Insert a chunk and its vec0 embedding row. Returns chunk id."""
    cur = conn.execute(
        "INSERT INTO chunks (document_id, chunk_index, text, token_count) VALUES (?, ?, ?, ?)",
        (document_id, chunk_index, text, len(text.split())),
    )
    conn.commit()
    chunk_id = cur.lastrowid
    blob = _pack(vec)
    conn.execute(
        "INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, blob),
    )
    conn.commit()
    return chunk_id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_basic_search_returns_results(db: sqlite3.Connection) -> None:
    """Insert known documents, search, verify results come back."""
    doc_id = _insert_doc(db, title="Philosophy Notes")
    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc_id, "Socrates was a philosopher", vec)

    query_vec = _pack([0.9, 0.1] + [0.0] * (_DIM - 2))
    results = search(db, query_vec)

    assert len(results) == 1
    assert results[0].document_id == doc_id
    assert results[0].chunk_text == "Socrates was a philosopher"
    assert results[0].title == "Philosophy Notes"
    assert isinstance(results[0].score, float)


def test_results_ranked_by_similarity(db: sqlite3.Connection) -> None:
    """Closer vectors should rank higher (lower distance)."""
    doc1 = _insert_doc(db, title="Close Doc")
    doc2 = _insert_doc(db, title="Far Doc")

    close_vec = [1.0] + [0.0] * (_DIM - 1)
    far_vec = [-1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc1, "close content", close_vec)
    _insert_chunk_with_embedding(db, doc2, "far content", far_vec)

    query = _pack([0.9, 0.1] + [0.0] * (_DIM - 2))
    results = search(db, query)

    assert len(results) == 2
    assert results[0].document_id == doc1
    assert results[1].document_id == doc2
    assert results[0].score < results[1].score


# ---------------------------------------------------------------------------
# Filter: content_type
# ---------------------------------------------------------------------------


def test_filter_by_content_type(db: sqlite3.Connection) -> None:
    """Only results matching the content_type filter should be returned."""
    doc_book = _insert_doc(db, content_type="book", title="A Book")
    doc_capture = _insert_doc(db, content_type="capture", title="A Capture")

    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc_book, "book content", vec)
    _insert_chunk_with_embedding(db, doc_capture, "capture content", vec)

    query = _pack(vec)
    results = search(db, query, content_type="book")

    assert len(results) == 1
    assert results[0].content_type == "book"
    assert results[0].document_id == doc_book


def test_filter_content_type_no_match(db: sqlite3.Connection) -> None:
    """When content_type filter excludes all docs, return empty."""
    doc = _insert_doc(db, content_type="capture")
    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc, "some text", vec)

    query = _pack(vec)
    results = search(db, query, content_type="podcast")
    assert results == []


# ---------------------------------------------------------------------------
# Filter: source
# ---------------------------------------------------------------------------


def test_filter_by_source(db: sqlite3.Connection) -> None:
    """Free-text source filter should match substring of source_uri."""
    doc1 = _insert_doc(db, source_uri="https://example.com/article/123")
    doc2 = _insert_doc(db, source_uri="https://other.com/post/456")

    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc1, "example content", vec)
    _insert_chunk_with_embedding(db, doc2, "other content", vec)

    query = _pack(vec)
    results = search(db, query, source="example.com")

    assert len(results) == 1
    assert results[0].document_id == doc1


# ---------------------------------------------------------------------------
# Filter: date range
# ---------------------------------------------------------------------------


def test_filter_by_date_range(db: sqlite3.Connection) -> None:
    """Only documents within the date range should be returned."""
    doc_old = _insert_doc(db, title="Old", created_at="2024-01-15T00:00:00Z")
    doc_new = _insert_doc(db, title="New", created_at="2025-06-15T00:00:00Z")

    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc_old, "old content", vec)
    _insert_chunk_with_embedding(db, doc_new, "new content", vec)

    query = _pack(vec)
    results = search(db, query, date_from="2025-01-01", date_to="2025-12-31")

    assert len(results) == 1
    assert results[0].document_id == doc_new


def test_filter_date_from_only(db: sqlite3.Connection) -> None:
    """date_from without date_to should filter correctly."""
    doc_old = _insert_doc(db, title="Old", created_at="2023-01-01T00:00:00Z")
    doc_new = _insert_doc(db, title="New", created_at="2025-06-01T00:00:00Z")

    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc_old, "old", vec)
    _insert_chunk_with_embedding(db, doc_new, "new", vec)

    query = _pack(vec)
    results = search(db, query, date_from="2025-01-01")

    assert len(results) == 1
    assert results[0].document_id == doc_new


def test_filter_date_to_only(db: sqlite3.Connection) -> None:
    """date_to without date_from should filter correctly."""
    doc_old = _insert_doc(db, title="Old", created_at="2023-01-01T00:00:00Z")
    doc_new = _insert_doc(db, title="New", created_at="2025-06-01T00:00:00Z")

    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc_old, "old", vec)
    _insert_chunk_with_embedding(db, doc_new, "new", vec)

    query = _pack(vec)
    results = search(db, query, date_to="2024-01-01")

    assert len(results) == 1
    assert results[0].document_id == doc_old


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


def test_empty_results_no_embeddings(db: sqlite3.Connection) -> None:
    """When no embeddings exist, search returns empty list."""
    query = _pack([1.0] + [0.0] * (_DIM - 1))
    results = search(db, query)
    assert results == []


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


def test_limit_respected(db: sqlite3.Connection) -> None:
    """Results should not exceed the requested limit."""
    for i in range(5):
        doc = _insert_doc(db, title=f"Doc {i}")
        vec = [float(i) * 0.1] + [0.0] * (_DIM - 1)
        _insert_chunk_with_embedding(db, doc, f"content {i}", vec)

    query = _pack([0.0] + [0.0] * (_DIM - 1))
    results = search(db, query, limit=3)

    assert len(results) <= 3


def test_limit_clamped_to_max(db: sqlite3.Connection) -> None:
    """Limit above 50 should be clamped to 50."""
    doc = _insert_doc(db)
    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc, "text", vec)

    query = _pack(vec)
    results = search(db, query, limit=100)
    # Should not error; internally clamped
    assert len(results) == 1


def test_limit_minimum_is_one(db: sqlite3.Connection) -> None:
    """Limit of 0 or negative should be clamped to 1."""
    doc = _insert_doc(db)
    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc, "text", vec)

    query = _pack(vec)
    results = search(db, query, limit=0)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Multiple content types
# ---------------------------------------------------------------------------


def test_multiple_content_types_in_results(db: sqlite3.Connection) -> None:
    """Without a content_type filter, results span multiple types."""
    doc_book = _insert_doc(db, content_type="book", title="My Book")
    doc_bluesky = _insert_doc(db, content_type="bluesky", title="Bluesky Post")
    doc_capture = _insert_doc(db, content_type="capture", title="Capture")

    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc_book, "book text", vec)
    _insert_chunk_with_embedding(db, doc_bluesky, "bluesky text", vec)
    _insert_chunk_with_embedding(db, doc_capture, "capture text", vec)

    query = _pack(vec)
    results = search(db, query)

    content_types = {r.content_type for r in results}
    assert len(content_types) >= 2  # at least two distinct types


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------


def test_search_result_fields(db: sqlite3.Connection) -> None:
    """Verify all SearchResult fields are populated."""
    doc = _insert_doc(
        db,
        content_type="article",
        title="Test Article",
        source_uri="https://example.com/article",
        source_id="ext-123",
        created_at="2025-03-15T10:00:00Z",
    )
    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc, "article body text", vec)

    query = _pack(vec)
    results = search(db, query)

    assert len(results) == 1
    r = results[0]
    assert r.document_id == doc
    assert r.content_type == "article"
    assert r.source_id == "ext-123"
    assert r.source_uri == "https://example.com/article"
    assert r.title == "Test Article"
    assert r.chunk_text == "article body text"
    assert r.created_at == "2025-03-15T10:00:00Z"
    assert isinstance(r.score, float)


# ---------------------------------------------------------------------------
# results_to_dicts helper
# ---------------------------------------------------------------------------


def test_results_to_dicts(db: sqlite3.Connection) -> None:
    """results_to_dicts should produce plain dicts from SearchResult objects."""
    doc = _insert_doc(db)
    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc, "hello world", vec)

    query = _pack(vec)
    results = search(db, query)
    dicts = results_to_dicts(results)

    assert len(dicts) == 1
    assert isinstance(dicts[0], dict)
    assert "score" in dicts[0]
    assert "chunk_text" in dicts[0]
    assert "document_id" in dicts[0]


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


def test_combined_filters(db: sqlite3.Connection) -> None:
    """Multiple filters applied together should all be respected."""
    doc_match = _insert_doc(
        db,
        content_type="book",
        source_uri="https://books.example.com/123",
        created_at="2025-05-01T00:00:00Z",
    )
    doc_wrong_type = _insert_doc(
        db,
        content_type="bluesky",
        source_uri="https://books.example.com/456",
        created_at="2025-05-01T00:00:00Z",
    )
    doc_wrong_date = _insert_doc(
        db,
        content_type="book",
        source_uri="https://books.example.com/789",
        created_at="2023-01-01T00:00:00Z",
    )

    vec = [1.0] + [0.0] * (_DIM - 1)
    _insert_chunk_with_embedding(db, doc_match, "match text", vec)
    _insert_chunk_with_embedding(db, doc_wrong_type, "wrong type", vec)
    _insert_chunk_with_embedding(db, doc_wrong_date, "wrong date", vec)

    query = _pack(vec)
    results = search(
        db,
        query,
        content_type="book",
        source="books.example.com",
        date_from="2025-01-01",
    )

    assert len(results) == 1
    assert results[0].document_id == doc_match
