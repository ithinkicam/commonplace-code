#!/usr/bin/env python3
"""Video metadata scan — walk movie and TV show directories and enqueue ingest jobs.

Usage
-----
    python scripts/video_metadata_scan.py [--dry-run] [--limit N]
        [--movies-dir PATH] [--tv-dir PATH]

Options
-------
--dry-run            Report counts and parsed names without enqueuing jobs.
--limit N            Cap the number of jobs enqueued per content type.
--movies-dir PATH    Override movies root (default: COMMONPLACE_MOVIES_DIR or
                     /Volumes/Expansion/Movies).
--tv-dir PATH        Override TV shows root (default: COMMONPLACE_TV_DIR or
                     /Volumes/Expansion/TV Shows).

Environment variables
---------------------
COMMONPLACE_MOVIES_DIR   Override movies directory.
COMMONPLACE_TV_DIR       Override TV shows directory.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MOVIES_PATH = "/Volumes/Expansion/Movies"
DEFAULT_TV_PATH = "/Volumes/Expansion/TV Shows"

# Suffixes we treat as standalone movie files (not directories)
VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg", ".ts"}

SKIP_PREFIXES = ("._",)
SKIP_NAMES = {".DS_Store", ".txt"}
SKIP_SUFFIXES = {".txt", ".nfo", ".jpg", ".png", ".srt", ".sub"}

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry discovery
# ---------------------------------------------------------------------------


def _is_video_file(p: Path) -> bool:
    """Return True if p is a standalone video file worth ingesting."""
    if p.name.startswith(SKIP_PREFIXES):
        return False
    if p.name in SKIP_NAMES:
        return False
    if p.suffix.lower() in SKIP_SUFFIXES:
        return False
    return p.suffix.lower() in VIDEO_SUFFIXES


def _should_skip_entry(entry: Path) -> bool:
    """Return True if this entry should be completely skipped."""
    if entry.name.startswith(SKIP_PREFIXES):
        return True
    if entry.name in SKIP_NAMES:
        return True
    return entry.suffix.lower() in SKIP_SUFFIXES


def _find_entries(root: Path, is_tv: bool) -> list[tuple[Path, bool]]:
    """Walk the root directory and return (path, is_tv) tuples for each entry.

    Rules:
    - Top-level directories → one entry each (season/series folder)
    - Top-level video files → one entry each (standalone movie)
    - Skip macOS junk, .txt files (torrent metadata), .nfo, subtitles

    Returns list of (path, is_tv) tuples.
    """
    entries: list[tuple[Path, bool]] = []

    try:
        items = sorted(root.iterdir())
    except PermissionError as exc:
        logger.error("cannot list %s: %s", root, exc)
        return []

    for entry in items:
        if _should_skip_entry(entry):
            logger.debug("SKIP (junk): %s", entry.name)
            continue

        if entry.is_dir():
            entries.append((entry, is_tv))
        elif entry.is_file():
            if _is_video_file(entry):
                entries.append((entry, is_tv))
            else:
                logger.debug("SKIP file (non-video): %s", entry.name)

    return entries


# ---------------------------------------------------------------------------
# Dry-run parser test
# ---------------------------------------------------------------------------


def _dry_run_parse(entries: list[tuple[Path, bool]]) -> list[str]:
    """Attempt to parse all entry names and return list of unparseable ones."""
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from commonplace_worker.handlers.video_filename import parse as parse_filename
    except ImportError as exc:
        logger.warning("could not import video_filename parser: %s", exc)
        return []

    unparseable: list[str] = []
    for entry_path, is_tv in entries:
        try:
            result = parse_filename(entry_path.name, is_tv=is_tv)
            logger.info(
                "WOULD ENQUEUE [%s] %s → title=%r year=%r",
                "TV" if is_tv else "movie",
                entry_path.name[:70],
                result["title"],
                result["year"],
            )
        except ValueError as exc:
            logger.warning("UNPARSEABLE: %s — %s", entry_path.name, exc)
            unparseable.append(entry_path.name)

    return unparseable


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan movie/TV directories and enqueue video metadata ingest jobs."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List entries and parse names; do not enqueue.",
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="Cap the number of jobs enqueued per content type.",
    )
    parser.add_argument(
        "--movies-dir",
        metavar="PATH",
        default=os.environ.get("COMMONPLACE_MOVIES_DIR", DEFAULT_MOVIES_PATH),
        help="Override the movies root path.",
    )
    parser.add_argument(
        "--tv-dir",
        metavar="PATH",
        default=os.environ.get("COMMONPLACE_TV_DIR", DEFAULT_TV_PATH),
        help="Override the TV shows root path.",
    )
    args = parser.parse_args(argv)

    movies_path = Path(args.movies_dir)
    tv_path = Path(args.tv_dir)

    # Check availability of at least one directory
    movies_ok = movies_path.exists()
    tv_ok = tv_path.exists()

    if not movies_ok and not tv_ok:
        logger.error(
            "Neither movies dir (%s) nor TV dir (%s) exists — is the drive mounted?",
            movies_path,
            tv_path,
        )
        return 1

    if not movies_ok:
        logger.warning("Movies path not found: %s — skipping", movies_path)
    if not tv_ok:
        logger.warning("TV path not found: %s — skipping", tv_path)

    # Discover entries
    all_movies: list[tuple[Path, bool]] = (
        _find_entries(movies_path, is_tv=False) if movies_ok else []
    )
    all_tv: list[tuple[Path, bool]] = (
        _find_entries(tv_path, is_tv=True) if tv_ok else []
    )
    all_entries = all_movies + all_tv

    total_movies = len(all_movies)
    total_tv = len(all_tv)
    total = len(all_entries)

    if args.dry_run:
        limited_movies = all_movies if args.limit is None else all_movies[: args.limit]
        limited_tv = all_tv if args.limit is None else all_tv[: args.limit]
        limited_entries = limited_movies + limited_tv

        unparseable = _dry_run_parse(limited_entries)

        limit_note = f" (limit={args.limit})" if args.limit is not None else ""
        print(
            f"\nSummary (dry-run{limit_note}): "
            f"found_movies={total_movies} "
            f"found_tv={total_tv} "
            f"total={total} "
            f"would_enqueue={len(limited_entries)} "
            f"unparseable={len(unparseable)}"
        )
        if unparseable:
            print("\nUnparseable filenames:")
            for name in unparseable:
                print(f"  {name}")
        return 0

    # Real run: open DB and enqueue
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from commonplace_db.db import connect, migrate
    from commonplace_server.jobs import submit

    conn = connect()
    migrate(conn)

    enqueued_movies = 0
    enqueued_tv = 0
    skipped_already = 0
    skipped_in_flight = 0
    errors = 0

    def _enqueue(entry_path: Path, job_kind: str, limit_counter: int) -> tuple[str, int]:
        """Attempt to enqueue one entry. Returns (action, new_counter).

        Two idempotency checks: (1) skip if the document is already fully
        enriched (``plot IS NOT NULL``); (2) skip if there's already a queued
        or running ``job_kind`` job with the same path. Without check (2), the
        scan runs on a launchd timer and re-enqueues every not-yet-processed
        path on each tick, producing 4× duplicates in the queue (seen in the
        2026-04-22 cleanup).
        """
        nonlocal skipped_already, skipped_in_flight, errors

        existing = conn.execute(
            "SELECT id FROM documents WHERE filesystem_path = ? AND plot IS NOT NULL",
            (str(entry_path),),
        ).fetchone()
        if existing is not None:
            skipped_already += 1
            logger.info(
                "SKIP %s — already enriched (document_id=%d)", entry_path.name, existing["id"]
            )
            return "skipped", limit_counter

        in_flight = conn.execute(
            "SELECT id FROM job_queue "
            "WHERE kind = ? AND status IN ('queued','running') "
            "AND json_extract(payload, '$.path') = ? LIMIT 1",
            (job_kind, str(entry_path)),
        ).fetchone()
        if in_flight is not None:
            skipped_in_flight += 1
            logger.info(
                "SKIP %s — already in queue (job_id=%d, kind=%s)",
                entry_path.name, in_flight["id"], job_kind,
            )
            return "skipped", limit_counter

        try:
            submit(conn, job_kind, {"path": str(entry_path)})
            logger.info("ENQUEUED [%s] %s", job_kind, entry_path.name)
            return "enqueued", limit_counter + 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.error("FAILED to enqueue %s: %s", entry_path.name, exc)
            return "error", limit_counter

    for entry_path, _is_tv in all_movies:
        if args.limit is not None and enqueued_movies >= args.limit:
            break
        _action, enqueued_movies = _enqueue(entry_path, "ingest_movie", enqueued_movies)

    for entry_path, _is_tv in all_tv:
        if args.limit is not None and enqueued_tv >= args.limit:
            break
        _action, enqueued_tv = _enqueue(entry_path, "ingest_tv", enqueued_tv)

    print(
        f"\nSummary: "
        f"found_movies={total_movies} "
        f"found_tv={total_tv} "
        f"enqueued_movies={enqueued_movies} "
        f"enqueued_tv={enqueued_tv} "
        f"skipped_already_enriched={skipped_already} "
        f"skipped_already_in_queue={skipped_in_flight} "
        f"errors={errors}"
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
