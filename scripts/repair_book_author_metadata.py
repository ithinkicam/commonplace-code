#!/usr/bin/env python3
"""Repair missing/placeholder book authors from conservative filename metadata.

By default this is a dry-run over embedded book documents. It uses the same
normalization helper as the live library ingest handler, so the repair pass and
future ingest behavior stay in sync.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import commonplace_db  # noqa: E402
from commonplace_worker.handlers.library import (  # noqa: E402
    _metadata_value_is_missing,
    _normalize_book_metadata,
)


@dataclass(frozen=True)
class MetadataRepair:
    document_id: int
    status: str
    source_path: str
    old_title: str | None
    new_title: str | None
    old_author: str | None
    new_author: str


def build_plan(conn: sqlite3.Connection, *, status: str | None) -> list[MetadataRepair]:
    """Find book rows whose author can be safely inferred from the source path."""
    where = "content_type = 'book'"
    params: list[str] = []
    if status is not None:
        where += " AND status = ?"
        params.append(status)

    rows = conn.execute(
        f"""
        SELECT id, title, author, raw_path, source_uri, status
        FROM documents
        WHERE {where}
        ORDER BY id
        """,
        params,
    ).fetchall()

    repairs: list[MetadataRepair] = []
    for row in rows:
        old_author = row["author"]
        if not _metadata_value_is_missing(old_author):
            continue

        source_path = row["raw_path"] or row["source_uri"]
        if not source_path:
            continue

        inferred_title, inferred_author = _normalize_book_metadata(
            Path(source_path),
            row["title"],
            old_author,
        )
        if _metadata_value_is_missing(inferred_author):
            continue

        old_title = row["title"]
        new_title = inferred_title if _metadata_value_is_missing(old_title) else old_title

        if new_title == old_title and inferred_author == old_author:
            continue

        repairs.append(
            MetadataRepair(
                document_id=int(row["id"]),
                status=str(row["status"]),
                source_path=str(source_path),
                old_title=old_title,
                new_title=new_title,
                old_author=old_author,
                new_author=str(inferred_author),
            )
        )
    return repairs


def _backup_db(db_path: Path) -> Path:
    """Create a same-host SQLite backup before mutating the live library."""
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"library.db.manual-book-metadata-repair.{timestamp}.bak"

    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def apply_plan(
    conn: sqlite3.Connection,
    db_path: Path,
    repairs: list[MetadataRepair],
    *,
    backup: bool,
) -> dict[str, object]:
    """Apply metadata repairs and return a machine-readable summary."""
    backup_path = _backup_db(db_path) if backup and repairs else None
    with conn:
        for repair in repairs:
            conn.execute(
                """
                UPDATE documents
                SET title = ?,
                    author = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = ?
                """,
                (repair.new_title, repair.new_author, repair.document_id),
            )

    return {
        "backup_path": str(backup_path) if backup_path is not None else None,
        "updated_documents": len(repairs),
        "document_ids": [repair.document_id for repair in repairs],
    }


def _print_plan(repairs: list[MetadataRepair], *, status: str | None) -> None:
    scope = "all statuses" if status is None else f"status={status!r}"
    print(f"candidate book metadata repairs ({scope}): {len(repairs)}")
    for repair in repairs:
        print(f"- #{repair.document_id} [{repair.status}] {Path(repair.source_path).name}")
        if repair.old_title != repair.new_title:
            print(f"  title:  {repair.old_title!r} -> {repair.new_title!r}")
        print(f"  author: {repair.old_author!r} -> {repair.new_author!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=commonplace_db.DB_PATH,
        help="Path to library.db.",
    )
    parser.add_argument(
        "--status",
        default="embedded",
        help="Only repair documents with this status. Default: embedded.",
    )
    parser.add_argument(
        "--all-statuses",
        action="store_true",
        help="Repair book documents regardless of status.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the repair. Without this flag, prints a dry-run plan.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run alias; this is the default when --apply is absent.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a manual sqlite backup before applying.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the plan as JSON instead of a human-readable list.",
    )
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")

    db_path = Path(args.db_path).expanduser()
    status = None if args.all_statuses else args.status

    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        repairs = build_plan(conn, status=status)
        if args.json:
            print(json.dumps([asdict(repair) for repair in repairs], indent=2))
        else:
            _print_plan(repairs, status=status)

        if not args.apply:
            print("\ndry-run only; rerun with --apply to mutate the database")
            return 0

        result = apply_plan(conn, db_path, repairs, backup=not args.no_backup)
    finally:
        conn.close()

    print("\napplied")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
