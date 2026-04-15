"""Tests for the migrations runner: freshness, idempotency, versioning."""

from __future__ import annotations

import sqlite3

import pytest

from commonplace_db import connect, migrate

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migrate_fresh_db_returns_nonzero_version() -> None:
    """A fresh in-memory DB should migrate to at least version 1."""
    conn = connect(":memory:")
    version = migrate(conn)
    assert version >= 1


def test_migrate_twice_is_idempotent() -> None:
    """Calling migrate() on an already-up-to-date DB returns the same version."""
    conn = connect(":memory:")
    v1 = migrate(conn)
    v2 = migrate(conn)
    assert v1 == v2


def test_schema_version_reflects_highest_migration(migrated_conn: sqlite3.Connection) -> None:
    """schema_version.version should equal the highest migration number applied."""
    version = migrate(migrated_conn)
    row = migrated_conn.execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()
    assert row is not None
    assert row["v"] == version


def test_each_migration_recorded_once() -> None:
    """Each migration version appears exactly once in schema_version."""
    conn = connect(":memory:")
    migrate(conn)
    rows = conn.execute("SELECT version, COUNT(*) AS cnt FROM schema_version GROUP BY version").fetchall()
    for row in rows:
        assert row["cnt"] == 1, f"Version {row['version']} recorded {row['cnt']} times"


def test_migrate_empty_db_creates_schema_version_table() -> None:
    """Even on an empty DB, migrate() creates schema_version before applying migrations."""
    conn = connect(":memory:")
    migrate(conn)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "schema_version" in names


def test_migrate_returns_int() -> None:
    conn = connect(":memory:")
    version = migrate(conn)
    assert isinstance(version, int)


def test_all_expected_tables_exist_after_migrate() -> None:
    """After migration, all domain tables must be present."""
    conn = connect(":memory:")
    migrate(conn)
    table_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    expected = {"schema_version", "documents", "chunks", "embeddings", "job_queue"}
    assert expected <= table_names, f"Missing tables: {expected - table_names}"


# ---------------------------------------------------------------------------
# Fixture shared with test_db_schema.py
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated_conn() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn
