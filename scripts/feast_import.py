#!/usr/bin/env python3
"""Feast importer — load feasts.yaml into the feast table (idempotent upsert).

Usage
-----
    python scripts/feast_import.py [OPTIONS]

Options
-------
--feasts PATH       Override default feasts.yaml path.
--subjects PATH     Override default theological_subjects.yaml path.
--db PATH           Override COMMONPLACE_DB_PATH / commonplace_db.DB_PATH.
--dry-run           Validate + report counts without touching the DB.
--ignore-missing-cross-refs
                    Warn instead of failing when a cross_tradition_equivalent
                    slug cannot be resolved to a feast in the current import.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
DEFAULT_FEASTS_PATH = _REPO_ROOT / "commonplace_db" / "seed" / "feasts.yaml"
DEFAULT_SUBJECTS_PATH = _REPO_ROOT / "commonplace_db" / "seed" / "theological_subjects.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _make_slug(primary_name: str, tradition: str) -> str:
    """Return a stable slug for a feast: ``{name_snake}_{tradition}``.

    Example: ``"Saint Mary the Virgin"`` + ``"anglican"``
    → ``"saint_mary_the_virgin_anglican"``
    """
    name_part = primary_name.lower()
    name_part = _NON_ALNUM_RE.sub("_", name_part).strip("_")
    return f"{name_part}_{tradition}"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _lookup_feast(
    conn: sqlite3.Connection, primary_name: str, tradition: str
) -> sqlite3.Row | None:
    """Return the existing feast row for (primary_name, tradition) or None."""
    result: sqlite3.Row | None = conn.execute(
        "SELECT id, primary_name, tradition, alternate_names, calendar_type, "
        "date_rule, precedence, theological_subjects, cross_tradition_equivalent_id, "
        "source, trial_use, created_at FROM feast "
        "WHERE primary_name = ? AND tradition = ?",
        (primary_name, tradition),
    ).fetchone()
    return result


def _row_needs_update(
    row: sqlite3.Row,
    alternate_names_json: str,
    calendar_type: str,
    date_rule: str,
    precedence: str,
    theological_subjects_json: str,
    source: str,
    trial_use: int,
) -> bool:
    """Return True if any mutable field differs from the DB row."""
    # trial_use is stored as SQLite INTEGER 0/1; coerce to int before compare
    # to be robust against None (migration-backfilled rows).
    row_trial_use = int(row["trial_use"]) if row["trial_use"] is not None else 0
    return (
        (row["alternate_names"] or "[]") != alternate_names_json
        or row["calendar_type"] != calendar_type
        or row["date_rule"] != date_rule
        or row["precedence"] != precedence
        or (row["theological_subjects"] or "[]") != theological_subjects_json
        or (row["source"] or "") != source
        or row_trial_use != trial_use
    )


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------


def _run_import(
    conn: sqlite3.Connection,
    entries: list,  # list[FeastEntry] — avoid top-level import to keep mypy happy
    *,
    dry_run: bool,
    ignore_missing_cross_refs: bool,
) -> tuple[int, int, int, int]:
    """Upsert feast entries into the DB.

    Returns
    -------
    (new, updated, unchanged, failed) counts.
    """
    new = updated = unchanged = failed = 0
    errors: list[str] = []

    # -----------------------------------------------------------------------
    # Pass 1: upsert every row WITHOUT cross_tradition_equivalent_id.
    # Build slug → feast_id map for pass 2.
    # -----------------------------------------------------------------------
    slug_to_id: dict[str, int] = {}

    for entry in entries:
        alt_json = json.dumps(entry.alternate_names)
        subj_json = json.dumps(entry.theological_subjects)
        slug = _make_slug(entry.primary_name, entry.tradition)
        trial_use_int = int(entry.trial_use)

        if dry_run:
            # Peek at the DB to compute what would change.
            row = _lookup_feast(conn, entry.primary_name, entry.tradition)
            if row is None:
                new += 1
            elif _row_needs_update(
                row, alt_json, entry.calendar_type, entry.date_rule,
                entry.precedence, subj_json, entry.source, trial_use_int
            ):
                updated += 1
                slug_to_id[slug] = row["id"]
            else:
                unchanged += 1
                slug_to_id[slug] = row["id"]
        else:
            row = _lookup_feast(conn, entry.primary_name, entry.tradition)
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO feast
                        (primary_name, alternate_names, tradition, calendar_type,
                         date_rule, precedence, theological_subjects,
                         source, trial_use)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.primary_name,
                        alt_json,
                        entry.tradition,
                        entry.calendar_type,
                        entry.date_rule,
                        entry.precedence,
                        subj_json,
                        entry.source,
                        trial_use_int,
                    ),
                )
                raw_id = cursor.lastrowid
                assert raw_id is not None
                feast_id: int = raw_id
                slug_to_id[slug] = feast_id
                new += 1
            else:
                feast_id = int(row["id"])
                slug_to_id[slug] = feast_id
                if _row_needs_update(
                    row, alt_json, entry.calendar_type, entry.date_rule,
                    entry.precedence, subj_json, entry.source, trial_use_int
                ):
                    conn.execute(
                        """
                        UPDATE feast SET
                            alternate_names = ?,
                            calendar_type = ?,
                            date_rule = ?,
                            precedence = ?,
                            theological_subjects = ?,
                            source = ?,
                            trial_use = ?,
                            updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (
                            alt_json,
                            entry.calendar_type,
                            entry.date_rule,
                            entry.precedence,
                            subj_json,
                            entry.source,
                            trial_use_int,
                            feast_id,
                        ),
                    )
                    updated += 1
                else:
                    unchanged += 1

    if not dry_run:
        conn.commit()

    # -----------------------------------------------------------------------
    # Pass 2: resolve cross_tradition_equivalent slugs and UPDATE rows.
    # -----------------------------------------------------------------------
    for entry in entries:
        if entry.cross_tradition_equivalent is None:
            continue

        ref_slug = entry.cross_tradition_equivalent
        ref_id = slug_to_id.get(ref_slug)

        if ref_id is None:
            msg = (
                f"feast {entry.primary_name!r} ({entry.tradition}): "
                f"cross_tradition_equivalent slug {ref_slug!r} could not be "
                "resolved to any feast in this import"
            )
            if ignore_missing_cross_refs:
                logger.warning("Ignoring unresolved cross-ref: %s", msg)
            else:
                errors.append(msg)
                failed += 1
            continue

        if dry_run:
            continue  # nothing to write

        # Find the feast id for the entry itself.
        entry_slug = _make_slug(entry.primary_name, entry.tradition)
        entry_id = slug_to_id.get(entry_slug)
        if entry_id is None:
            # The row was counted but slug may not be in map if dry_run path changed it —
            # look it up from DB.
            row = _lookup_feast(conn, entry.primary_name, entry.tradition)
            entry_id = row["id"] if row else None

        if entry_id is not None:
            conn.execute(
                "UPDATE feast SET cross_tradition_equivalent_id = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (ref_id, entry_id),
            )

    if not dry_run:
        conn.commit()

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        raise RuntimeError(
            f"{len(errors)} unresolved cross_tradition_equivalent reference(s). "
            "Pass --ignore-missing-cross-refs to warn instead of failing."
        )

    return new, updated, unchanged, failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import feasts.yaml into the feast table (idempotent)."
    )
    parser.add_argument(
        "--feasts",
        metavar="PATH",
        default=str(DEFAULT_FEASTS_PATH),
        help="Path to feasts.yaml (default: commonplace_db/seed/feasts.yaml).",
    )
    parser.add_argument(
        "--subjects",
        metavar="PATH",
        default=str(DEFAULT_SUBJECTS_PATH),
        help="Path to theological_subjects.yaml (default: commonplace_db/seed/theological_subjects.yaml).",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="Override COMMONPLACE_DB_PATH / DB_PATH.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report counts without touching the DB.",
    )
    parser.add_argument(
        "--ignore-missing-cross-refs",
        action="store_true",
        help="Warn (not fail) when a cross_tradition_equivalent slug is unresolvable.",
    )

    args = parser.parse_args(argv)

    feasts_path = Path(args.feasts)
    subjects_path = Path(args.subjects)

    # ------------------------------------------------------------------
    # Apply --db override before importing commonplace_db so DB_PATH is
    # picked up if connect() uses the module-level default.
    # ------------------------------------------------------------------
    if args.db:
        os.environ["COMMONPLACE_DB_PATH"] = args.db

    import commonplace_db
    from commonplace_db.feast_schema import FeastValidationError, validate_feasts

    if args.db:
        commonplace_db.DB_PATH = args.db

    # ------------------------------------------------------------------
    # Validate YAML
    # ------------------------------------------------------------------
    try:
        entries = validate_feasts(feasts_path, subjects_path)
    except FeastValidationError as exc:
        print("Feast YAML validation failed:", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    if not entries:
        print("Imported 0 feasts (0 new, 0 updated, 0 unchanged, 0 failed)")
        return 0

    # ------------------------------------------------------------------
    # Connect + migrate
    # ------------------------------------------------------------------
    db_path = args.db or commonplace_db.DB_PATH
    conn = commonplace_db.connect(db_path)
    commonplace_db.migrate(conn)

    # ------------------------------------------------------------------
    # Run import
    # ------------------------------------------------------------------
    try:
        new, updated, unchanged, failed = _run_import(
            conn,
            entries,
            dry_run=args.dry_run,
            ignore_missing_cross_refs=args.ignore_missing_cross_refs,
        )
    except RuntimeError as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    total = new + updated + unchanged
    prefix = "Would import" if args.dry_run else "Imported"
    print(
        f"{prefix} {total} feasts "
        f"({new} new, {updated} updated, {unchanged} unchanged, {failed} failed)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
