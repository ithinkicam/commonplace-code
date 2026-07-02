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


# ---------------------------------------------------------------------------
# Migration 0007 — liturgical ingest tables
# ---------------------------------------------------------------------------


def test_migration_0007_version(migrated_conn: sqlite3.Connection) -> None:
    """After full migration, schema_version MAX should be 17 (latest)."""
    row = migrated_conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    assert row is not None
    assert row["v"] == 17


def test_migration_0007_integrity_check(migrated_conn: sqlite3.Connection) -> None:
    """PRAGMA integrity_check must return 'ok' after all migrations apply."""
    row = migrated_conn.execute("PRAGMA integrity_check").fetchone()
    assert row is not None
    assert row[0] == "ok"


def test_liturgical_unit_meta_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "liturgical_unit_meta" in _table_names(migrated_conn)


def test_feast_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "feast" in _table_names(migrated_conn)


def test_commemoration_bio_table_exists(migrated_conn: sqlite3.Connection) -> None:
    assert "commemoration_bio" in _table_names(migrated_conn)


def test_liturgical_unit_meta_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "liturgical_unit_meta")
    required = {
        "document_id",
        "category",
        "genre",
        "tradition",
        "source",
        "language_register",
        "office",
        "office_position",
        "calendar_anchor_id",
        "canonical_id",
        "raw_metadata",
    }
    assert required <= cols, f"Missing columns: {required - cols}"


def test_feast_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "feast")
    required = {
        "id",
        "primary_name",
        "alternate_names",
        "tradition",
        "calendar_type",
        "date_rule",
        "precedence",
        "theological_subjects",
        "cross_tradition_equivalent_id",
        "created_at",
        "updated_at",
    }
    assert required <= cols, f"Missing columns: {required - cols}"


def test_commemoration_bio_columns(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "commemoration_bio")
    required = {"id", "feast_id", "document_id", "text", "source"}
    assert required <= cols, f"Missing columns: {required - cols}"


def test_liturgical_unit_meta_indexes(migrated_conn: sqlite3.Connection) -> None:
    indexes = _index_names(migrated_conn)
    expected = {
        "idx_liturgical_meta_category",
        "idx_liturgical_meta_genre",
        "idx_liturgical_meta_tradition",
        "idx_liturgical_meta_feast",
        "idx_liturgical_meta_canonical",
    }
    assert expected <= indexes, f"Missing indexes: {expected - indexes}"


def test_feast_indexes(migrated_conn: sqlite3.Connection) -> None:
    indexes = _index_names(migrated_conn)
    expected = {"idx_feast_tradition", "idx_feast_date_rule"}
    assert expected <= indexes, f"Missing indexes: {expected - indexes}"


def test_commemoration_bio_index(migrated_conn: sqlite3.Connection) -> None:
    indexes = _index_names(migrated_conn)
    assert "idx_bio_feast" in indexes


def test_feast_created_at_default(migrated_conn: sqlite3.Connection) -> None:
    """Inserting a feast row without created_at/updated_at should use the datetime default."""
    migrated_conn.execute(
        "INSERT INTO feast (primary_name, tradition, calendar_type, date_rule, precedence)"
        " VALUES (?, ?, ?, ?, ?)",
        ("All Saints' Day", "anglican", "fixed", "11-01", "principal_feast"),
    )
    migrated_conn.commit()
    row = migrated_conn.execute(
        "SELECT created_at, updated_at FROM feast WHERE primary_name = 'All Saints'' Day'"
    ).fetchone()
    assert row is not None
    assert row["created_at"] is not None
    assert row["updated_at"] is not None


def test_liturgical_unit_meta_fk_cascade(migrated_conn: sqlite3.Connection) -> None:
    """Deleting a document should cascade-delete its liturgical_unit_meta row."""
    migrated_conn.execute("PRAGMA foreign_keys = ON")
    migrated_conn.execute(
        "INSERT INTO documents (content_type) VALUES (?)",
        ("liturgical_unit",),
    )
    migrated_conn.commit()
    doc_id = migrated_conn.execute(
        "SELECT id FROM documents WHERE content_type = 'liturgical_unit'"
    ).fetchone()["id"]
    migrated_conn.execute(
        "INSERT INTO liturgical_unit_meta (document_id, category, genre, tradition, source)"
        " VALUES (?, ?, ?, ?, ?)",
        (doc_id, "liturgical_proper", "collect", "anglican", "bcp_1979"),
    )
    migrated_conn.commit()
    migrated_conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    migrated_conn.commit()
    meta = migrated_conn.execute(
        "SELECT * FROM liturgical_unit_meta WHERE document_id = ?", (doc_id,)
    ).fetchone()
    assert meta is None, "CASCADE DELETE should have removed the liturgical_unit_meta row"


def test_therapy_session_meta_schema(migrated_conn: sqlite3.Connection) -> None:
    """Therapy sessions store type-specific metadata in a dedicated table."""
    cols = _column_names(migrated_conn, "therapy_session_meta")
    assert {
        "document_id",
        "session_date",
        "therapist",
        "session_type",
        "notion_page_id",
        "notion_url",
        "notion_last_edited_at",
    }.issubset(cols)

    indexes = {
        row["name"]
        for row in migrated_conn.execute("PRAGMA index_list(therapy_session_meta)")
    }
    assert "idx_therapy_session_meta_session_date" in indexes


def test_scheduled_runs_schema(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "scheduled_runs")
    assert {"name", "status", "details", "started_at", "completed_at"}.issubset(cols)


def test_conversation_summary_meta_schema(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "conversation_summary_meta")
    assert {
        "document_id",
        "conversation_date",
        "platform",
        "source_url",
        "model",
        "topics",
        "captured_at",
    }.issubset(cols)

    indexes = {
        row["name"]
        for row in migrated_conn.execute("PRAGMA index_list(conversation_summary_meta)")
    }
    assert "idx_conversation_summary_meta_date" in indexes
    assert "idx_conversation_summary_meta_platform" in indexes


def test_surface_invocations_schema(migrated_conn: sqlite3.Connection) -> None:
    cols = _column_names(migrated_conn, "surface_invocations")
    assert {
        "seed",
        "mode",
        "types",
        "requested_limit",
        "similarity_floor",
        "recency_bias",
        "raw_candidate_count",
        "floor_candidate_count",
        "judge_status",
        "note",
        "error",
        "rejected_count",
        "accepted_json",
        "triangulation_json",
        "candidates_json",
        "elapsed_ms",
        "invocation_status",
        "stage",
        "judge_error_kind",
        "updated_at",
        "completed_at",
        "created_at",
    }.issubset(cols)

    indexes = {
        row["name"]
        for row in migrated_conn.execute("PRAGMA index_list(surface_invocations)")
    }
    assert "idx_surface_invocations_created" in indexes
    assert "idx_surface_invocations_mode_created" in indexes
    assert "idx_surface_invocations_judge_status" in indexes
    assert "idx_surface_invocations_status_created" in indexes
    assert "idx_surface_invocations_stage_created" in indexes


def test_feast_self_referential_fk(migrated_conn: sqlite3.Connection) -> None:
    """cross_tradition_equivalent_id can reference another feast row."""
    migrated_conn.execute("PRAGMA foreign_keys = ON")
    migrated_conn.execute(
        "INSERT INTO feast (primary_name, tradition, calendar_type, date_rule, precedence)"
        " VALUES (?, ?, ?, ?, ?)",
        ("Saint Mary the Virgin", "anglican", "fixed", "08-15", "holy_day"),
    )
    migrated_conn.commit()
    feast_id = migrated_conn.execute(
        "SELECT id FROM feast WHERE primary_name = 'Saint Mary the Virgin'"
    ).fetchone()["id"]
    migrated_conn.execute(
        "INSERT INTO feast (primary_name, tradition, calendar_type, date_rule, precedence,"
        " cross_tradition_equivalent_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("Dormition of the Theotokos", "byzantine", "fixed", "08-15", "principal_feast", feast_id),
    )
    migrated_conn.commit()
    row = migrated_conn.execute(
        "SELECT cross_tradition_equivalent_id FROM feast WHERE primary_name = 'Dormition of the Theotokos'"
    ).fetchone()
    assert row is not None
    assert row["cross_tradition_equivalent_id"] == feast_id


def test_commemoration_bio_fk_to_feast(migrated_conn: sqlite3.Connection) -> None:
    """commemoration_bio.feast_id must reference feast(id)."""
    migrated_conn.execute("PRAGMA foreign_keys = ON")
    migrated_conn.execute(
        "INSERT INTO feast (primary_name, tradition, calendar_type, date_rule, precedence)"
        " VALUES (?, ?, ?, ?, ?)",
        ("All Saints' Day", "anglican", "fixed", "11-01", "principal_feast"),
    )
    migrated_conn.commit()
    feast_id = migrated_conn.execute(
        "SELECT id FROM feast WHERE primary_name = 'All Saints'' Day'"
    ).fetchone()["id"]
    migrated_conn.execute(
        "INSERT INTO commemoration_bio (feast_id, text, source) VALUES (?, ?, ?)",
        (feast_id, "A feast of all the saints.", "bcp_1979"),
    )
    migrated_conn.commit()
    row = migrated_conn.execute(
        "SELECT feast_id FROM commemoration_bio WHERE feast_id = ?", (feast_id,)
    ).fetchone()
    assert row is not None
    assert row["feast_id"] == feast_id
