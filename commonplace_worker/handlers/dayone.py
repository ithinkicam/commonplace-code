"""Day One journal ingest handler.

``handle_dayone_ingest(payload, conn)`` reads from the Day One app's local
SQLite store (``~/Library/Group Containers/5U8NS4GX82.dayoneapp2/Data/
Documents/DayOne.sqlite``) and upserts one ``documents`` row per entry,
embedding the markdown body so the text surfaces via semantic search.

Payload modes
-------------
``{"mode": "backfill"}``
    Sweep all entries with a non-empty ``ZMARKDOWNTEXT``.

``{"mode": "since", "iso": "2026-04-01T00:00:00Z"}``
    Only process entries whose ``ZMODIFIEDDATE`` is at or after the given
    ISO-8601 timestamp. Used by the periodic launchd agent to keep the
    worker's SELECT cheap.

Behavior
--------
- Dedup via ``(content_type='dayone_entry', source_id=ZUUID)`` unique index.
- Re-embed on edit: content_hash = ``sha256(ZMARKDOWNTEXT + ZMODIFIEDDATE)``
  changes when the entry's text or edit timestamp changes; when it does,
  the existing row is DELETED (chunks cascade) and re-inserted fresh.
- Never writes back to DayOne.sqlite — opened ``mode=ro`` via URI.
- Tests inject ``_dayone_db_path`` to point at a fixture SQLite file.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Apple Core Data reference date: 2001-01-01 00:00:00 UTC.
# ZCREATIONDATE / ZMODIFIEDDATE are stored as seconds-since-reference.
_CORE_DATA_EPOCH_OFFSET = 978_307_200


def _default_dayone_db_path() -> Path:
    """Return the Mac-default DayOne.sqlite path, or the override from env."""
    env = os.environ.get("COMMONPLACE_DAYONE_DB_PATH")
    if env:
        return Path(env)
    return Path.home() / (
        "Library/Group Containers/5U8NS4GX82.dayoneapp2/"
        "Data/Documents/DayOne.sqlite"
    )


def _core_data_to_unix(seconds_since_2001: float | None) -> float | None:
    if seconds_since_2001 is None:
        return None
    return seconds_since_2001 + _CORE_DATA_EPOCH_OFFSET


def _iso_to_core_data(iso: str) -> float:
    """Convert an ISO-8601 timestamp to Core Data seconds-since-2001.

    Accepts ``Z`` suffix (treated as UTC) and timezone-aware strings.
    """
    # Tolerate both "...Z" and "...+00:00" shapes
    normalized = iso.replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"bad ISO-8601 for Day One cutoff: {iso!r}") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.timestamp() - _CORE_DATA_EPOCH_OFFSET


def _derive_title(markdown: str) -> str:
    """First non-empty line, stripped of leading ``# `` heading markers and
    capped at 80 chars. Day One entries often begin with an H1."""
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Strip leading `#` heading markers
        while line.startswith("#"):
            line = line[1:]
        line = line.strip()
        if line:
            return line[:80]
    return "(untitled Day One entry)"


def _fetch_entries(
    dayone_db_path: Path,
    *,
    since_core_data: float | None,
) -> list[dict[str, Any]]:
    """Open DayOne.sqlite read-only and return a list of entry dicts.

    Returns (possibly-empty) list with keys: uuid, markdown, created_at_unix,
    modified_at_unix, journal_name, starred.
    """
    if not dayone_db_path.exists():
        raise FileNotFoundError(
            f"DayOne.sqlite not found at {dayone_db_path} — "
            "is Day One installed, or is COMMONPLACE_DAYONE_DB_PATH mis-set?"
        )

    # Open read-only via URI so we don't contend with the app. ``immutable=1``
    # would be even safer but would lock us out when the app holds the WAL;
    # ``mode=ro`` is enough for concurrent-read safety.
    uri = f"file:{dayone_db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        params: list[Any] = []
        sql = (
            "SELECT e.ZUUID            AS uuid, "
            "       e.ZMARKDOWNTEXT    AS markdown, "
            "       e.ZCREATIONDATE    AS created_core, "
            "       e.ZMODIFIEDDATE    AS modified_core, "
            "       e.ZSTARRED         AS starred, "
            "       j.ZNAME            AS journal_name "
            "FROM ZENTRY e "
            "LEFT JOIN ZJOURNAL j ON j.Z_PK = e.ZJOURNAL "
            "WHERE e.ZMARKDOWNTEXT IS NOT NULL AND e.ZMARKDOWNTEXT != ''"
        )
        if since_core_data is not None:
            sql += " AND e.ZMODIFIEDDATE >= ?"
            params.append(since_core_data)
        sql += " ORDER BY e.ZMODIFIEDDATE"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    entries: list[dict[str, Any]] = []
    for row in rows:
        entries.append(
            {
                "uuid": row["uuid"],
                "markdown": row["markdown"],
                "created_at_unix": _core_data_to_unix(row["created_core"]),
                "modified_at_unix": _core_data_to_unix(row["modified_core"]),
                "journal_name": row["journal_name"] or "",
                "starred": bool(row["starred"]),
            }
        )
    return entries


def _upsert_entry(
    conn: sqlite3.Connection,
    entry: dict[str, Any],
    *,
    _embedder: Any = None,
) -> tuple[str, int | None]:
    """Insert or replace one entry; returns (action, document_id).

    ``action`` is one of ``"inserted" | "updated" | "skipped"``. A skipped
    entry had an identical ``content_hash`` already in ``documents``.
    """
    uuid = entry["uuid"]
    markdown = entry["markdown"]
    modified = entry["modified_at_unix"] or 0.0

    # content_hash ties text + last-edit timestamp; edits → new hash → re-embed.
    hash_input = f"{markdown}|{modified:.6f}".encode()
    content_hash = hashlib.sha256(hash_input).hexdigest()

    # Is there an existing row for this uuid?
    existing = conn.execute(
        "SELECT id, content_hash FROM documents "
        "WHERE content_type='dayone_entry' AND source_id=?",
        (uuid,),
    ).fetchone()

    if existing is not None and existing[1] == content_hash:
        return ("skipped", int(existing[0]))

    # Drop any existing row so chunks cascade out and we can re-embed.
    if existing is not None:
        with conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (int(existing[0]),))

    title = _derive_title(markdown)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, source_id, title, content_hash, status)
            VALUES ('dayone_entry', ?, ?, ?, ?, 'ingesting')
            """,
            (f"dayone://{uuid}", uuid, title, content_hash),
        )
    doc_id: int = int(cur.lastrowid)  # type: ignore[arg-type]

    # Embed via the standard pipeline so chunks + embeddings behave like
    # any other content type.
    from commonplace_server.pipeline import embed_document

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    embed_document(doc_id, markdown, conn, **embed_kwargs)

    action = "updated" if existing is not None else "inserted"
    return (action, doc_id)


def handle_dayone_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _dayone_db_path: Path | None = None,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Sweep Day One entries into the commonplace DB.

    Payload: ``{"mode": "backfill"}`` or ``{"mode": "since", "iso": "..."}``.
    Returns ``{inserted, updated, skipped, elapsed_ms}``.
    """
    t0 = time.monotonic()

    mode = payload.get("mode", "backfill")
    since_core: float | None = None
    if mode == "since":
        iso = payload.get("iso")
        if not isinstance(iso, str):
            raise ValueError("dayone since mode requires {'iso': '<ISO-8601>'}")
        since_core = _iso_to_core_data(iso)
    elif mode != "backfill":
        raise ValueError(f"unknown dayone ingest mode: {mode!r}")

    dayone_db_path = _dayone_db_path or _default_dayone_db_path()
    entries = _fetch_entries(dayone_db_path, since_core_data=since_core)

    inserted = 0
    updated = 0
    skipped = 0
    for entry in entries:
        try:
            action, _doc_id = _upsert_entry(conn, entry, _embedder=_embedder)
        except Exception as exc:  # noqa: BLE001
            # Per-entry failures should not kill the whole sweep.
            logger.error(
                "failed to ingest dayone entry uuid=%s: %s", entry["uuid"], exc
            )
            continue
        if action == "inserted":
            inserted += 1
        elif action == "updated":
            updated += 1
        else:
            skipped += 1

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "dayone ingest complete mode=%s inserted=%d updated=%d skipped=%d elapsed_ms=%.0f",
        mode,
        inserted,
        updated,
        skipped,
        elapsed_ms,
    )
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "elapsed_ms": elapsed_ms,
    }
