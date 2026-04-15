"""Tests verifying that the expected tables and columns exist after migration."""

from __future__ import annotations

import sqlite3

import pytest

from commonplace_db import connect, migrate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated_conn() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def test_schema_version_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "schema_version" in _table_names(migrated_conn)


def test_documents_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "documents" in _table_names(migrated_conn)


def test_chunks_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "chunks" in _table_names(migrated_conn)


def test_embeddings_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "embeddings" in _table_names(migrated_conn)


def test_job_queue_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "job_queue" in _table_names(migrated_conn)


def test_documents_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "documents")
    required = {
        "id",
        "content_type",
        "source_uri",
        "title",
        "author",
        "content_hash",
        "raw_path",
        "status",
        "created_at",
        "updated_at",
    }
    assert required <= cols, f"Missing columns: {required - cols}"


def test_chunks_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "chunks")
    required = {"id", "document_id", "chunk_index", "text", "created_at"}
    assert required <= cols, f"Missing columns: {required - cols}"


def test_embeddings_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "embeddings")
    required = {"id", "chunk_id", "model", "vector_blob", "created_at"}
    assert required <= cols, f"Missing columns: {required - cols}"


def test_job_queue_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "job_queue")
    required = {
        "id",
        "kind",
        "payload",
        "status",
        "attempts",
        "error",
        "created_at",
        "started_at",
        "completed_at",
    }
    assert required <= cols, f"Missing columns: {required - cols}"


def test_job_queue_index_exists(migrated_conn: sqlite3.Connection) -> None:
    indexes = _index_names(migrated_conn)
    assert "idx_job_queue_status_created" in indexes


def test_schema_version_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "schema_version")
    assert {"version", "applied_at"} <= cols


def test_job_queue_default_status(migrated_conn: sqlite3.Connection) -> None:
    """Inserting a minimal job row should default status to 'queued'."""
    migrated_conn.execute(
        "INSERT INTO job_queue (kind, payload) VALUES (?, ?)",
        ("test_kind", '{"foo": 1}'),
    )
    migrated_conn.commit()
    row = migrated_conn.execute("SELECT status FROM job_queue WHERE kind = 'test_kind'").fetchone()
    assert row is not None
    assert row["status"] == "queued"


def test_documents_default_status(migrated_conn: sqlite3.Connection) -> None:
    """Documents should default to 'pending' status."""
    migrated_conn.execute(
        "INSERT INTO documents (content_type) VALUES (?)",
        ("capture",),
    )
    migrated_conn.commit()
    row = migrated_conn.execute(
        "SELECT status FROM documents WHERE content_type = 'capture'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
