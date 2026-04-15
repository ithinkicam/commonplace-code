#!/usr/bin/env python3
"""import_storygraph.py — one-shot import of a StoryGraph library CSV export.

Each row with a "read" / "finished" status becomes a ``documents`` row with
``content_type='storygraph_entry'``, carrying title, author, rating, read_date,
and source_id.  No embedding is performed — StoryGraph rows have no body text.

Usage
-----
  python scripts/import_storygraph.py path/to/storygraph_export.csv
  python scripts/import_storygraph.py path/to/storygraph_export.csv --dry-run

Exit codes
----------
  0  success (including dry-run)
  1  fatal error (bad CSV path, unexpected schema, etc.)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column name aliases
# StoryGraph's export format has varied across releases; we try common names.
# ---------------------------------------------------------------------------

# Each entry is a tuple of candidate column names (tried left to right).
_COL_TITLE = ("Title",)
_COL_AUTHORS = ("Authors",)
_COL_RATING = ("Star Rating",)
_COL_STATUS = ("Read Status",)
_COL_READ_DATE = ("Last Date Read", "Dates Read")
_COL_SOURCE_ID = ("StoryGraph ID", "ID")

_READ_STATUSES = {"read", "finished"}


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    """Return the first candidate key present in *row*, or None."""
    for key in candidates:
        if key in row:
            return row[key].strip() or None
    return None


def _content_hash(title: str, authors: str) -> str:
    """SHA-256 of 'title\\nauthors' — dedup fallback when source_id is absent."""
    payload = f"{title}\n{authors}".encode()
    return hashlib.sha256(payload).hexdigest()


def _parse_rating(raw: str | None) -> float | None:
    """Convert a StoryGraph star-rating string like '4.25' → float, or None."""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_read_date(raw: str | None) -> str | None:
    """Return the first ISO-ish date from the field, or None.

    StoryGraph sometimes puts multiple dates separated by '|' in the
    'Dates Read' field.  We take the first one.
    """
    if not raw:
        return None
    # Take the first segment if pipe-separated.
    first = raw.split("|")[0].strip()
    if not first:
        return None
    # Basic sanity: expect 'YYYY-MM-DD' or 'YYYY/MM/DD'.
    normalized = first.replace("/", "-")
    # Warn but keep non-conforming strings rather than silently dropping.
    parts = normalized.split("-")
    if len(parts) != 3:
        _log.warning("Unexpected date format %r — storing as-is", first)
    return normalized


def _detect_columns(fieldnames: list[str]) -> None:
    """Warn about any expected column groups that are entirely absent."""
    groups = [
        ("Title", _COL_TITLE),
        ("Authors", _COL_AUTHORS),
        ("Read Status", _COL_STATUS),
    ]
    for label, candidates in groups:
        if not any(c in fieldnames for c in candidates):
            _log.warning(
                "Could not find %r column (tried: %s). Available columns: %s",
                label,
                ", ".join(candidates),
                ", ".join(fieldnames),
            )


# ---------------------------------------------------------------------------
# Core import logic
# ---------------------------------------------------------------------------


def run_import(
    csv_path: Path,
    conn: Any,  # sqlite3.Connection — typed as Any to avoid hard import at top
    dry_run: bool = False,
) -> dict[str, int]:
    """Parse *csv_path* and insert qualifying rows into *conn*.

    Returns a summary dict with keys:
        rows_read, inserted, skipped_existing, skipped_unread, warnings
    """
    import sqlite3  # noqa: PLC0415 — imported here so the module is importable without sqlite3

    rows_read = 0
    inserted = 0
    skipped_existing = 0
    skipped_unread = 0
    warnings = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            _log.error("CSV appears to be empty or has no header row.")
            sys.exit(1)

        fieldnames: list[str] = list(reader.fieldnames)
        _detect_columns(fieldnames)

        for row in reader:
            rows_read += 1

            # --- Read status filter -------------------------------------------
            raw_status = _pick(row, _COL_STATUS)
            if raw_status is None or raw_status.lower() not in _READ_STATUSES:
                skipped_unread += 1
                continue

            # --- Required fields ----------------------------------------------
            title = _pick(row, _COL_TITLE)
            if not title:
                _log.warning("Row %d: missing title — skipping.", rows_read)
                warnings += 1
                skipped_unread += 1
                continue

            raw_authors = _pick(row, _COL_AUTHORS)
            authors = raw_authors if raw_authors else ""

            # --- Optional fields ---------------------------------------------
            rating = _parse_rating(_pick(row, _COL_RATING))

            raw_date_col = None
            for col in _COL_READ_DATE:
                if col in row:
                    raw_date_col = row[col].strip() or None
                    break
            read_date = _parse_read_date(raw_date_col)

            source_id = _pick(row, _COL_SOURCE_ID)

            chash = _content_hash(title, authors)

            if dry_run:
                _log.info(
                    "[DRY-RUN] Would insert: title=%r author=%r rating=%s "
                    "read_date=%s source_id=%s",
                    title,
                    authors,
                    rating,
                    read_date,
                    source_id,
                )
                inserted += 1
                continue

            # --- Insert ------------------------------------------------------
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO documents
                        (content_type, title, author, rating, read_date,
                         source_id, content_hash, status)
                    VALUES
                        ('storygraph_entry', ?, ?, ?, ?, ?, ?, 'complete')
                    """,
                    (title, authors, rating, read_date, source_id, chash),
                )
                if conn.execute("SELECT changes()").fetchone()[0] == 0:
                    skipped_existing += 1
                else:
                    inserted += 1
            except sqlite3.IntegrityError as exc:
                _log.warning("Row %d: integrity error (%s) — skipping.", rows_read, exc)
                warnings += 1
                skipped_existing += 1

    if not dry_run:
        conn.commit()

    return {
        "rows_read": rows_read,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "skipped_unread": skipped_unread,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Import a StoryGraph library CSV export into the Commonplace DB."
    )
    parser.add_argument("csv", type=Path, help="Path to the StoryGraph CSV export file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be imported without writing to the database.",
    )
    args = parser.parse_args(argv)

    csv_path: Path = args.csv
    if not csv_path.exists():
        _log.error("CSV file not found: %s", csv_path)
        sys.exit(1)
    if not csv_path.is_file():
        _log.error("Path is not a file: %s", csv_path)
        sys.exit(1)

    if args.dry_run:
        _log.info("--- DRY RUN — no data will be written ---")
        # In dry-run mode we still need a DB connection for the schema, but we
        # won't commit anything.  Use an in-memory DB so the real DB is untouched.

        from commonplace_db import connect, migrate

        conn = connect(":memory:")
        migrate(conn)
    else:
        from commonplace_db import connect, migrate

        conn = connect()
        migrate(conn)

    summary = run_import(csv_path, conn, dry_run=args.dry_run)

    print("\n--- StoryGraph import summary ---")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if args.dry_run:
        print("(dry-run: no rows written)")


if __name__ == "__main__":
    main()
