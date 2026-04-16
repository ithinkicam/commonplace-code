#!/usr/bin/env python3
"""Audiobooks scan — walk /Volumes/Expansion/Audiobooks/ and enqueue ingest jobs.

Usage
-----
    python scripts/audiobooks_scan.py [--dry-run] [--limit N] [--audiobooks-path PATH]

Options
-------
--dry-run            Report counts without enqueuing jobs.
--limit N            Cap the number of jobs enqueued (for early testing).
--audiobooks-path    Override the default audiobooks root (for testing).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_AUDIOBOOKS_PATH = "/Volumes/Expansion/Audiobooks"

AUDIO_SUFFIXES = {".m4b", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".opus"}
SKIP_PREFIXES = ("._",)
SKIP_NAMES = {".DS_Store"}

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Book discovery
# ---------------------------------------------------------------------------


def _is_audio_file(p: Path) -> bool:
    """Return True if p is an ingestible audio file."""
    if p.name.startswith(SKIP_PREFIXES):
        return False
    if p.name in SKIP_NAMES:
        return False
    return p.suffix.lower() in AUDIO_SUFFIXES


def _find_books(audiobooks_path: Path) -> list[Path]:
    """Walk the audiobooks root and return one path per logical book.

    Rules:
    - Top-level audio files → one book each (bare .m4b)
    - Top-level directories → one book each (may contain multiple audio parts)
    - Skip macOS junk: ._* files, .DS_Store
    - Skip subdirectories that contain zero audio files (cover-art-only dirs)
    """
    books: list[Path] = []

    try:
        entries = sorted(audiobooks_path.iterdir())
    except PermissionError as exc:
        logger.error("cannot list %s: %s", audiobooks_path, exc)
        return []

    for entry in entries:
        # Skip macOS resource-fork files
        if entry.name.startswith(SKIP_PREFIXES):
            continue
        if entry.name in SKIP_NAMES:
            continue

        if entry.is_file():
            if _is_audio_file(entry):
                books.append(entry)
            else:
                logger.debug("SKIP file (non-audio): %s", entry.name)
            continue

        if entry.is_dir():
            # Check whether the directory has any audio files (direct children only)
            has_audio = any(
                _is_audio_file(child)
                for child in entry.iterdir()
                if child.is_file()
            )
            if has_audio:
                books.append(entry)
            else:
                # May be a nested author folder (e.g. Becky Chambers/ > A Prayer...)
                # Walk one level deeper
                nested_books = _scan_nested_dir(entry)
                if nested_books:
                    books.extend(nested_books)
                else:
                    logger.debug("SKIP dir (no audio): %s", entry.name)

    return books


def _scan_nested_dir(author_dir: Path) -> list[Path]:
    """Scan one level deeper for book sub-directories."""
    nested: list[Path] = []
    try:
        for sub in sorted(author_dir.iterdir()):
            if sub.name.startswith(SKIP_PREFIXES) or sub.name in SKIP_NAMES:
                continue
            if sub.is_dir():
                has_audio = any(
                    _is_audio_file(child)
                    for child in sub.iterdir()
                    if child.is_file()
                )
                if has_audio:
                    nested.append(sub)
    except PermissionError as exc:
        logger.warning("cannot list %s: %s", author_dir, exc)
    return nested


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan audiobooks folder and enqueue ingest_audiobook jobs."
    )
    parser.add_argument("--dry-run", action="store_true", help="List books; do not enqueue.")
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="Cap the number of books to enqueue.",
    )
    parser.add_argument(
        "--audiobooks-path",
        metavar="PATH",
        default=DEFAULT_AUDIOBOOKS_PATH,
        help="Override the audiobooks root path.",
    )
    args = parser.parse_args(argv)

    audiobooks_path = Path(args.audiobooks_path)

    # Mount / existence check
    if not audiobooks_path.exists():
        logger.error(
            "Audiobooks path does not exist: %s — is the external drive mounted?",
            audiobooks_path,
        )
        return 1

    # Discover books
    all_books = _find_books(audiobooks_path)
    total_found = len(all_books)

    if args.dry_run:
        # Apply limit in dry-run mode too
        limited_books = all_books if args.limit is None else all_books[: args.limit]
        for book in limited_books:
            logger.info("WOULD ENQUEUE %s", book.name)

        # Extract and display sample metadata for spot-checking
        _dry_run_sample(limited_books[:10])

        limit_note = f" (limit={args.limit})" if args.limit is not None else ""
        to_enqueue = len(limited_books)
        print(
            f"\nSummary (dry-run{limit_note}): "
            f"found={total_found} "
            f"would_enqueue={to_enqueue}"
        )
        return 0

    # Real run: open DB and enqueue
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from commonplace_db.db import connect, migrate
    from commonplace_server.jobs import submit

    conn = connect()
    migrate(conn)

    enqueued: list[Path] = []
    skipped_already: list[Path] = []

    for book in all_books:
        if args.limit is not None and len(enqueued) >= args.limit:
            break

        # Check idempotency: skip if already has a document row
        existing = conn.execute(
            "SELECT id FROM documents WHERE source_uri = ?", (str(book),)
        ).fetchone()
        if existing is not None:
            skipped_already.append(book)
            logger.info("SKIP %s — already ingested (document_id=%d)", book.name, existing["id"])
            continue

        submit(conn, "ingest_audiobook", {"path": str(book), "inbox_file": None})
        enqueued.append(book)
        logger.info("ENQUEUED %s", book.name)

    print(
        f"\nSummary: found={total_found} "
        f"enqueued={len(enqueued)} "
        f"skipped_already_ingested={len(skipped_already)}"
    )
    return 0


def _dry_run_sample(books: list[Path]) -> None:
    """Log sample metadata extraction for the first few books (dry-run only)."""
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from commonplace_worker.handlers.audiobooks import (
            _collect_audio_files,
            _extract_tags,
            _parse_dir_name,
        )

        logger.info("--- Sample metadata extraction (first %d books) ---", len(books))
        for book in books:
            if book.is_file():
                tag_meta = _extract_tags(book)
                dir_meta = _parse_dir_name(book.stem)
            else:
                audio_files = _collect_audio_files(book)
                tag_meta = _extract_tags(audio_files[0]) if audio_files else {}
                dir_meta = _parse_dir_name(book.name)

            title = tag_meta.get("title") or dir_meta.get("title")
            author = tag_meta.get("author") or dir_meta.get("author")
            logger.info("  %-60s → title=%r author=%r", book.name[:60], title, author)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sample extraction failed: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
