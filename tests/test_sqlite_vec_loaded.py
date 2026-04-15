"""Verify sqlite-vec extension loads and KNN queries work correctly."""

from __future__ import annotations

import sqlite3
import struct

import pytest

from commonplace_db import connect, migrate

_DIM = 768


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def test_sqlite_vec_version_available(db: sqlite3.Connection) -> None:
    row = db.execute("SELECT vec_version()").fetchone()
    assert row is not None
    assert row[0].startswith("v")


def test_chunk_vectors_table_exists(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE name = 'chunk_vectors'"
    ).fetchall()
    assert len(rows) == 1


def test_knn_query_returns_expected_order() -> None:
    """Insert 3 toy vectors; query closest to vec_a and verify ranking."""
    conn = connect(":memory:")
    migrate(conn)

    # Insert a document and 3 chunks into real tables first
    conn.execute("INSERT INTO documents (content_type) VALUES ('capture')")
    conn.commit()
    doc_id = conn.execute("SELECT id FROM documents").fetchone()[0]

    chunk_ids = []
    for i in range(3):
        cur = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, token_count) VALUES (?, ?, ?, ?)",
            (doc_id, i, f"chunk {i}", 2),
        )
        conn.commit()
        chunk_ids.append(cur.lastrowid)

    # vec_a = [1, 0, 0, …]  vec_b = [0, 1, 0, …]  vec_c = [-1, 0, 0, …]
    # Query vector close to vec_a: [0.9, 0.1, …]
    vec_a = [1.0] + [0.0] * (_DIM - 1)
    vec_b = [0.0, 1.0] + [0.0] * (_DIM - 2)
    vec_c = [-1.0] + [0.0] * (_DIM - 1)
    query = [0.9, 0.1] + [0.0] * (_DIM - 2)

    for cid, vec in zip(chunk_ids, [vec_a, vec_b, vec_c]):
        conn.execute(
            "INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (?, ?)",
            (cid, _pack(vec)),
        )
    conn.commit()

    rows = conn.execute(
        "SELECT chunk_id, distance FROM chunk_vectors WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
        (_pack(query),),
    ).fetchall()

    assert len(rows) == 3
    # Closest to [0.9, 0.1, …] should be vec_a ([1, 0, …])
    assert rows[0]["chunk_id"] == chunk_ids[0]
    # vec_c ([-1, 0, …]) should be farthest
    assert rows[-1]["chunk_id"] == chunk_ids[2]


def test_migration_idempotent_no_duplicate_schema_version(db: sqlite3.Connection) -> None:
    """Running migrate twice on the same connection must not duplicate rows."""
    from commonplace_db import migrate

    before = db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    migrate(db)  # second call
    after = db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    # Idempotency guarantee: second migrate adds no rows, regardless of
    # how many migrations exist at this point in Phase 2.
    assert before == after
