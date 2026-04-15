"""Database connection factory and migration runner for Commonplace.

Public API
----------
connect(db_path)  -> sqlite3.Connection
migrate(conn)     -> int   (new schema version after applying any pending migrations)
DB_PATH           str      default path; overridable via COMMONPLACE_DB_PATH env var
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Default path — ~/commonplace/library.db, env-var overridable
# ---------------------------------------------------------------------------
DB_PATH: str = os.environ.get(
    "COMMONPLACE_DB_PATH",
    str(Path.home() / "commonplace" / "library.db"),
)

# The directory that holds numbered .sql migration files.
_MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and configure) a SQLite connection to *db_path*.

    If *db_path* is None the resolved default (DB_PATH / COMMONPLACE_DB_PATH)
    is used. Accepts str, pathlib.Path, or the special value ":memory:".

    Settings applied:
    - WAL journal mode for concurrent reader/writer access.
    - synchronous=NORMAL — safe for WAL, good durability/perf trade-off.
    - foreign_keys=ON — enforce FK constraints.
    - Row factory set to sqlite3.Row for dict-style access.
    """
    resolved: str = str(db_path) if db_path is not None else DB_PATH
    if resolved != ":memory:":
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any un-applied migrations to *conn* and return the new schema version.

    Idempotent: calling migrate() on an already-up-to-date database is a no-op
    and returns the current version without touching any tables.

    Migration files must be named ``NNNN_<description>.sql`` (four-digit prefix,
    e.g. ``0001_initial.sql``) and live under ``commonplace_db/migrations/``.
    They are applied in lexicographic order; each is run exactly once, tracked
    by the ``schema_version`` table.
    """
    # Ensure the schema_version tracking table exists.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
        """
    )
    conn.commit()

    # Collect already-applied versions.
    applied: set[int] = {
        row[0] for row in conn.execute("SELECT version FROM schema_version")
    }

    # Collect available migration files, sorted by name (lexicographic == numeric
    # order when zero-padded).
    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))

    for mf in migration_files:
        version = _parse_version(mf.name)
        if version in applied:
            continue

        sql = mf.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (version,),
        )
        conn.commit()
        applied.add(version)

    current_version = max(applied) if applied else 0
    return current_version


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_version(filename: str) -> int:
    """Extract the numeric version prefix from a migration filename.

    E.g. ``0001_initial.sql`` → ``1``.
    Raises ValueError if the filename doesn't start with a four-digit prefix.
    """
    stem = filename.split("_")[0]
    if not stem.isdigit():
        raise ValueError(f"Migration filename must start with a numeric prefix: {filename!r}")
    return int(stem)
