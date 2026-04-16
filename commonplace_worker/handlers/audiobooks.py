"""Audiobook filesystem ingest handler.

handle_audiobook_ingest(payload, conn) is the worker handler for the
'ingest_audiobook' job kind.  It walks a book directory (or bare file),
extracts metadata via mutagen tags then directory-name parsing, fuzzy-merges
with existing storygraph_entry rows, and inserts a documents row with
content_type='audiobook'.  No transcription — metadata-only per v5 plan.

Job payload: {"path": "/abs/path/to/book/dir_or_file", "inbox_file": null}
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Mutagen is a required dep (pinned in pyproject.toml); import at module level
# so tests can patch it via 'commonplace_worker.handlers.audiobooks.MutagenFile'.
try:
    from mutagen import File as MutagenFile
except ImportError:  # pragma: no cover
    MutagenFile = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUDIO_SUFFIXES = {".m4b", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".opus"}
SKIP_PREFIXES = {"._"}
SKIP_NAMES = {".DS_Store"}

# Unicode colon substitute (U+A789) found in some directory names
_UNICODE_COLON = "\ua789"

# Patterns to strip from titles/dirs
_TRAILING_BRACKET_RE = re.compile(r"\s*[\[\(][^\]\)]*[\]\)]\s*$")
_ENCODING_SUFFIX_RE = re.compile(
    r"[-_](AAX|LC|MP3|M4B|AAC|FLAC|WAV|OGG|OPUS)"
    r"(_\d+)*(_[\d]+)*(_stereo|_mono)?"
    r"\s*$",
    re.IGNORECASE,
)
_NARRATOR_BRACKET_RE = re.compile(r"\s*\[([^\]]+)\]\s*$")

# Fuzzy-match threshold: normalised title similarity (simple token overlap)
_FUZZY_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class AudiobookDriveNotMounted(RuntimeError):
    """Raised when /Volumes/Expansion/ is not mounted."""


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_audiobook_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Worker handler for 'ingest_audiobook' jobs.

    Parameters
    ----------
    payload:
        Must contain ``path`` — absolute path to a book directory or audio file.
    conn:
        Open SQLite connection with migrations applied.

    Returns
    -------
    dict with keys: document_id, action (inserted|matched|skipped), elapsed_ms.
    """
    t0 = time.monotonic()

    path_str = payload.get("path")
    if not isinstance(path_str, str) or not path_str:
        raise ValueError(f"ingest_audiobook payload missing 'path': {payload!r}")

    book_path = Path(path_str)

    # Mount check
    _check_drive_mounted(book_path)

    if not book_path.exists():
        raise FileNotFoundError(f"audiobook path not found: {book_path}")

    # Resolve to canonical entry: directory or single file
    if book_path.is_file():
        if not _is_audio_file(book_path):
            logger.warning("skipping non-audio file: %s", book_path)
            return {"document_id": None, "action": "skipped", "elapsed_ms": 0.0}
        audio_files = [book_path]
        dir_name = book_path.stem
    else:
        dir_name = book_path.name
        audio_files = _collect_audio_files(book_path)
        if not audio_files:
            logger.warning("no audio files found in: %s", book_path)
            return {"document_id": None, "action": "skipped", "elapsed_ms": 0.0}

    # Idempotency: check if this path already has a document row
    existing_doc = conn.execute(
        "SELECT id FROM documents WHERE source_uri = ?", (str(book_path),)
    ).fetchone()
    if existing_doc is not None:
        doc_id: int = existing_doc["id"]
        logger.info("audiobook already ingested (source_uri match), document_id=%d", doc_id)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"document_id": doc_id, "action": "skipped", "elapsed_ms": elapsed_ms}

    # Extract metadata: mutagen first, then directory name
    tag_meta = _extract_tags(audio_files[0])
    dir_meta = _parse_dir_name(dir_name)

    title = tag_meta.get("title") or dir_meta.get("title")
    author = tag_meta.get("author") or dir_meta.get("author")
    narrator = tag_meta.get("narrator")

    if not title:
        logger.warning("could not extract title for %s — using raw dir name", book_path)
        title = _normalize_title(dir_name)

    logger.debug(
        "extracted metadata: title=%r author=%r narrator=%r path=%s",
        title,
        author,
        narrator,
        book_path,
    )

    # Fuzzy-merge against storygraph_entry rows
    storygraph_id = _fuzzy_merge(conn, title, author)

    if storygraph_id is not None:
        # Update storygraph_entry with audiobook_path and narrator
        _update_storygraph_entry(conn, storygraph_id, str(book_path), narrator)
        action = "matched"
        logger.info(
            "fuzzy-matched storygraph_entry id=%d for title=%r", storygraph_id, title
        )
        # Still insert an audiobook document row for search/classify_book
        document_id = _insert_audiobook_document(
            conn, str(book_path), title, author, narrator
        )
    else:
        # Unmatched: insert new storygraph_entry + audiobook document
        action = "inserted"
        _insert_storygraph_entry(conn, title, author, str(book_path), narrator)
        document_id = _insert_audiobook_document(
            conn, str(book_path), title, author, narrator
        )
        logger.info("inserted new audiobook document_id=%d title=%r", document_id, title)

    # Enqueue classify_book so the book note pipeline can fire later
    _enqueue_classify_book(conn, document_id, title, author)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "audiobook ingested: document_id=%d action=%s elapsed_ms=%.0f path=%s",
        document_id,
        action,
        elapsed_ms,
        book_path,
    )
    return {"document_id": document_id, "action": action, "elapsed_ms": elapsed_ms}


# ---------------------------------------------------------------------------
# Drive mount check
# ---------------------------------------------------------------------------


def _check_drive_mounted(path: Path) -> None:
    """Raise AudiobookDriveNotMounted if the external drive isn't available.

    We check by testing whether /Volumes/Expansion/ exists as a mount point.
    If the path is under /Volumes/Expansion/ and the mount is absent, we raise.
    Otherwise (e.g. test paths), we skip the check.
    """
    expansion = Path("/Volumes/Expansion")
    path_str = str(path)
    if "/Volumes/Expansion" in path_str and not expansion.exists():
        raise AudiobookDriveNotMounted(
            "/Volumes/Expansion/ is not mounted — cannot ingest audiobooks. "
            "Attach the external drive and retry."
        )


# ---------------------------------------------------------------------------
# Audio file collection
# ---------------------------------------------------------------------------


def _is_audio_file(p: Path) -> bool:
    """Return True if the file is an audio file we should ingest."""
    if p.name.startswith(tuple(SKIP_PREFIXES)):
        return False
    if p.name in SKIP_NAMES:
        return False
    return p.suffix.lower() in AUDIO_SUFFIXES


def _collect_audio_files(directory: Path) -> list[Path]:
    """Return all audio files in *directory* (non-recursive), skipping macOS junk."""
    files: list[Path] = []
    try:
        for p in sorted(directory.iterdir()):
            if not p.is_file():
                continue
            if _is_audio_file(p):
                files.append(p)
    except PermissionError as exc:
        logger.warning("permission error scanning %s: %s", directory, exc)
    return files


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def _extract_tags(audio_file: Path) -> dict[str, str | None]:
    """Extract title, author, narrator from mutagen tags.

    Returns a dict with keys 'title', 'author', 'narrator' (all may be None).
    Silently returns empty dict on read errors.
    """
    if MutagenFile is None:
        logger.warning("mutagen not installed — skipping tag extraction")
        return {}

    try:
        mf = MutagenFile(str(audio_file))
    except Exception as exc:
        logger.debug("mutagen could not read %s: %s", audio_file, exc)
        return {}

    if mf is None or mf.tags is None:
        return {}

    tags = mf.tags

    # MP4/M4B tags use FourCC keys
    def _mp4_get(key: str) -> str | None:
        val = tags.get(key)
        if val and hasattr(val, "__iter__") and not isinstance(val, str):
            val = list(val)
            return str(val[0]).strip() or None
        return None

    # ID3 (MP3) tags use text frame keys
    def _id3_get(key: str) -> str | None:
        frame = tags.get(key)
        if frame is None:
            return None
        if hasattr(frame, "text") and frame.text:
            return str(frame.text[0]).strip() or None
        return None

    # Try MP4 style first (m4b, m4a)
    title = _mp4_get("©nam") or _mp4_get("\xa9nam")
    author = _mp4_get("©ART") or _mp4_get("\xa9ART") or _mp4_get("aART")
    narrator = _mp4_get("©nrt") or _mp4_get("©wrp")

    # Fallback: ID3 style (mp3)
    if not title:
        title = _id3_get("TIT2")
    if not author:
        author = _id3_get("TPE1") or _id3_get("TPE2")
    if not narrator:
        narrator = _id3_get("TPE3")

    return {
        "title": title,
        "author": author,
        "narrator": narrator,
    }


def _normalize_title(raw: str) -> str:
    """Clean up a raw title string: strip codec suffixes, brackets, unicode colon."""
    # Replace unicode colon substitute with regular colon
    s = raw.replace(_UNICODE_COLON, ":")
    # Strip codec encoding suffixes like -LC_64_22050_stereo
    s = _ENCODING_SUFFIX_RE.sub("", s)
    # Strip trailing [year format] / (Unabridged) style brackets
    s = _TRAILING_BRACKET_RE.sub("", s)
    # Normalize underscores to spaces
    s = s.replace("_", " ")
    # Collapse multiple spaces
    s = re.sub(r" {2,}", " ", s)
    return s.strip()


def _parse_dir_name(dir_name: str) -> dict[str, str | None]:
    """Parse a directory name into title and author.

    Handles patterns:
    - "{Author} - {Title}"          (most common with subdirs)
    - "{Author} - {Title} - {Subtitle}" (multi-dash variant)
    - "{Title}"                     (bare, author-less)
    - Trailing [year format], (Unabridged), [Narrator Name] stripped
    - Unicode colon U+A789 normalized to ":"
    """
    raw = dir_name

    # Replace unicode colon substitute
    raw = raw.replace(_UNICODE_COLON, ":")

    # Strip trailing narrator bracket like [Samuel West]
    narrator_match = _NARRATOR_BRACKET_RE.search(raw)
    if narrator_match:
        raw = raw[: narrator_match.start()].strip()

    # Strip trailing [year format] or (Unabridged) style descriptors
    raw = _TRAILING_BRACKET_RE.sub("", raw).strip()

    # Strip codec encoding suffixes
    raw = _ENCODING_SUFFIX_RE.sub("", raw).strip()

    # Normalize underscores to spaces
    raw = raw.replace("_", " ")
    raw = re.sub(r" {2,}", " ", raw).strip()

    # Try "Author - Title" split
    # Allow for double spaces around dash (Alison Rumfitt  - Tell Me...)
    dash_match = re.match(r"^(.+?)\s+-\s+(.+)$", raw)
    if dash_match:
        potential_author = dash_match.group(1).strip()
        potential_title = dash_match.group(2).strip()
        # Heuristic: if the first segment looks like an author (has a space or
        # comma — "First Last" or "Last, First"), treat it as author
        # Single-word authors are unusual; single-word titles are common
        if re.search(r"[\s,]", potential_author):
            return {"author": potential_author, "title": potential_title}
        # Single-word author-like token still assigned as author if it looks
        # like a name (starts with capital)
        if re.match(r"^[A-Z]", potential_author):
            return {"author": potential_author, "title": potential_title}

    # No recognizable "Author - Title" pattern: treat the whole thing as title
    return {"author": None, "title": raw}


# ---------------------------------------------------------------------------
# Fuzzy merge
# ---------------------------------------------------------------------------


def _normalize_for_match(s: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace for comparison."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _token_overlap(a: str, b: str) -> float:
    """Jaccard-ish token overlap between two normalized strings."""
    if not a or not b:
        return 0.0
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union else 0.0


def _fuzzy_merge(
    conn: sqlite3.Connection,
    title: str | None,
    author: str | None,
) -> int | None:
    """Return the documents.id of the best-matching storygraph_entry, or None.

    Matches on normalized title with a Jaccard threshold, then optionally
    tightens on author if both sides have one.
    """
    if not title:
        return None

    norm_title = _normalize_for_match(title)
    norm_author = _normalize_for_match(author)

    rows = conn.execute(
        "SELECT id, title, author FROM documents WHERE content_type = 'storygraph_entry'"
    ).fetchall()

    best_id: int | None = None
    best_score: float = 0.0

    for row in rows:
        row_title = _normalize_for_match(row["title"])
        title_score = _token_overlap(norm_title, row_title)

        if title_score < _FUZZY_THRESHOLD:
            continue

        # If both sides have an author, factor it in
        row_author = _normalize_for_match(row["author"])
        if norm_author and row_author:
            author_score = _token_overlap(norm_author, row_author)
            # Combined: weighted title 70%, author 30%
            score = 0.7 * title_score + 0.3 * author_score
        else:
            score = title_score

        if score > best_score:
            best_score = score
            best_id = row["id"]

    return best_id


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------


def _update_storygraph_entry(
    conn: sqlite3.Connection,
    storygraph_id: int,
    audiobook_path: str,
    narrator: str | None,
) -> None:
    """Update an existing storygraph_entry row with audiobook path and narrator."""
    with conn:
        conn.execute(
            """
            UPDATE documents
               SET audiobook_path = ?,
                   narrator       = ?,
                   updated_at     = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE id = ?
            """,
            (audiobook_path, narrator, storygraph_id),
        )


def _insert_storygraph_entry(
    conn: sqlite3.Connection,
    title: str | None,
    author: str | None,
    audiobook_path: str,
    narrator: str | None,
) -> int:
    """Insert a new storygraph_entry row for an unmatched audiobook."""
    content_hash = _title_author_hash(title or "", author or "")
    with conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (content_type, title, author, audiobook_path, narrator,
                 content_hash, status)
            VALUES
                ('storygraph_entry', ?, ?, ?, ?, ?, 'complete')
            """,
            (title, author, audiobook_path, narrator, content_hash),
        )
    return cur.lastrowid or 0


def _insert_audiobook_document(
    conn: sqlite3.Connection,
    source_uri: str,
    title: str | None,
    author: str | None,
    narrator: str | None,
) -> int:
    """Insert a documents row with content_type='audiobook'.

    Uses source_uri as the unique key so re-running is idempotent.
    """
    content_hash = _path_hash(source_uri)
    with conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (content_type, source_uri, title, author, narrator,
                 audiobook_path, content_hash, status)
            VALUES
                ('audiobook', ?, ?, ?, ?, ?, ?, 'complete')
            """,
            (source_uri, title, author, narrator, source_uri, content_hash),
        )
    if cur.lastrowid and cur.lastrowid > 0:
        return cur.lastrowid

    # Row already existed (IGNORE fired); fetch its id
    row = conn.execute(
        "SELECT id FROM documents WHERE source_uri = ?", (source_uri,)
    ).fetchone()
    return row["id"] if row else 0


def _enqueue_classify_book(
    conn: sqlite3.Connection,
    document_id: int,
    title: str | None,
    author: str | None,
) -> None:
    """Submit a classify_book job for this audiobook document."""
    import json

    payload = json.dumps(
        {
            "document_id": document_id,
            "title": title,
            "author": author,
            "content_type": "audiobook",
        }
    )
    with conn:
        conn.execute(
            "INSERT INTO job_queue (kind, payload, status, attempts) VALUES (?, ?, 'queued', 0)",
            ("classify_book", payload),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _title_author_hash(title: str, author: str) -> str:
    """SHA-256 of 'title\\nauthor' — dedup key for new storygraph_entry rows."""
    payload = f"{title}\n{author}".encode()
    return hashlib.sha256(payload).hexdigest()


def _path_hash(path: str) -> str:
    """SHA-256 of the path string — unique key for audiobook document rows."""
    return hashlib.sha256(path.encode()).hexdigest()
