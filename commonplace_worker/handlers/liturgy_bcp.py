"""BCP 1979 liturgical ingest handler.

``handle_liturgy_bcp_ingest(payload, conn)`` is the worker handler for the
``ingest_liturgy_bcp`` job kind.  One submitted job ingests ALL BCP 1979
parsed units from up to five parsers:

  collects              → bcp_collects.parse_collects_dir
  daily_office          → bcp_daily_office.parse_daily_office_file
  psalter               → bcp_psalter.parse_psalter_file
  proper_liturgies      → bcp_proper_liturgies.parse_proper_liturgies_dir
  prayers_and_thanksgivings → bcp_prayers_and_thanksgivings.parse_prayers_and_thanksgivings

Per-unit insertion flow (§2.3 of the liturgical-ingest plan):
  1. Compute SHA-256 content_hash on body text.
  2. INSERT OR IGNORE INTO documents (content_type='liturgical_unit').
  3. Retrieve document_id (lastrowid or SELECT).
  4. INSERT OR IGNORE INTO liturgical_unit_meta.
  5. embed_document(document_id, body_text, conn).

Idempotency is provided by the UNIQUE index on (content_type, source_id)
from migration 0003.  Re-running is a no-op on already-ingested units.

Transaction strategy: one transaction per parser, committed after each
parser completes.  A failure in parser N does not roll back parsers 1…N-1.

Author choice: None (null).  The BCP 1979 is a liturgical text compiled by
the Episcopal Church; attributing it to "The Episcopal Church 1979" would be
technically accurate but misleads retrieval (it is not a monograph with an
author).  Null is idiomatic for liturgical corpora.  Adjust if desired.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_ROOT = "~/commonplace/cache/bcp_1979/www.bcponline.org/"

ALL_PARSERS: list[str] = [
    "collects",
    "daily_office",
    "psalter",
    "proper_liturgies",
    "prayers_and_thanksgivings",
]

# Author value for all BCP 1979 units.  Null — see module docstring.
_BCP_AUTHOR: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _upsert_document(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    source_uri: str,
    title: str,
    content_hash: str,
) -> tuple[int, bool]:
    """INSERT OR IGNORE a liturgical_unit document row.

    Note: the documents table has no ``content`` column — body text is stored
    in ``chunks`` via ``embed_document``.

    The documents table has a UNIQUE constraint on content_hash (migration 0001).
    Two different liturgical units can share identical body text (e.g., a short
    doxology).  When the INSERT is blocked by a content_hash collision on a row
    with a different source_id, we INSERT the document without content_hash so
    both units get their own row and their own embedding.

    Returns (document_id, was_inserted).
    """
    with conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (content_type, source_id, source_uri, title, author,
                 content_hash, status)
            VALUES ('liturgical_unit', ?, ?, ?, ?, ?, 'pending')
            """,
            (source_id, source_uri, title, _BCP_AUTHOR, content_hash),
        )
    if cursor.lastrowid and cursor.rowcount == 1:
        return cursor.lastrowid, True

    # Check if blocked by source_id uniqueness (idempotency case).
    row = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'liturgical_unit' AND source_id = ?",
        (source_id,),
    ).fetchone()
    if row is not None:
        # Row with this source_id already exists — this is a skip (idempotent).
        return int(row["id"]), False

    # Row was blocked only by content_hash collision (a different source_id row
    # has the same hash).  Insert this unit without content_hash so both units
    # get independent rows, chunks, and embeddings.
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_id, source_uri, title, author,
                 status)
            VALUES ('liturgical_unit', ?, ?, ?, ?, 'pending')
            """,
            (source_id, source_uri, title, _BCP_AUTHOR),
        )
    return cursor.lastrowid, True  # type: ignore[return-value]


def _upsert_meta(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    category: str,
    genre: str,
    tradition: str,
    source: str,
    language_register: str | None,
    office: str | None,
    office_position: str | None,
    canonical_id: str | None,
    raw_metadata: str,
) -> None:
    """INSERT OR IGNORE into liturgical_unit_meta."""
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO liturgical_unit_meta
                (document_id, category, genre, tradition, source,
                 language_register, office, office_position,
                 calendar_anchor_id, canonical_id, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                document_id,
                category,
                genre,
                tradition,
                source,
                language_register,
                office,
                office_position,
                canonical_id,
                raw_metadata,
            ),
        )


def _normalise_language_register(rite: str | None) -> str | None:
    """Map parser rite values to language_register schema values."""
    if rite in ("rite_i",):
        return "rite_i"
    if rite in ("rite_ii",):
        return "rite_ii"
    # "both", "none", None, or other → null
    return None


def _normalise_office(office: str | None) -> str | None:
    """Map parser office values to schema-legal values.

    Schema allows: morning_prayer | evening_prayer | eucharist |
    compline | noonday | other | NULL.
    """
    _PASS_THROUGH = {
        "morning_prayer",
        "evening_prayer",
        "compline",
        "noonday",
    }
    if office in _PASS_THROUGH:
        return office
    if office in ("daily_devotions", "canticle", "great_litany"):
        return "other"
    return None


def _raw_meta_to_str(meta: dict[str, Any] | str | None) -> str:
    """Normalise raw_metadata to a JSON string regardless of parser output shape."""
    if meta is None:
        return "{}"
    if isinstance(meta, str):
        return meta
    return json.dumps(meta, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Per-parser ingestion functions
# ---------------------------------------------------------------------------


def _ingest_collects(
    source_root: Path,
    conn: sqlite3.Connection,
    dry_run: bool,
    _embedder: Any,
) -> tuple[int, int, list[str]]:
    """Ingest BCP 1979 collects. Returns (inserted, skipped, errors)."""
    from commonplace_server.liturgical_parsers.bcp_collects import parse_collects_dir

    collects_dir = source_root / "Prayers" / "Collects"
    if not collects_dir.exists():
        # Fall back to the test-fixture path layout
        collects_dir = source_root / "collects"
    if not collects_dir.exists():
        msg = f"Collects directory not found under {source_root}"
        logger.warning(msg)
        return 0, 0, [msg]

    units = parse_collects_dir(collects_dir)
    inserted = skipped = 0
    errors: list[str] = []

    if dry_run:
        return len(units), 0, []

    from commonplace_server.pipeline import embed_document

    for unit in units:
        try:
            source_id = f"{unit.feast_slug}__{unit.rite}"
            source_uri = f"bcp1979://collects/{unit.section}/{unit.feast_slug}"
            doc_id, was_inserted = _upsert_document(
                conn,
                source_id=source_id,
                source_uri=source_uri,
                title=unit.feast_name,
                content_hash=_sha256_text(unit.body_text),
            )
            if was_inserted:
                _upsert_meta(
                    conn,
                    document_id=doc_id,
                    category="liturgical_proper",
                    genre="collect",
                    tradition="anglican",
                    source="bcp_1979",
                    language_register=_normalise_language_register(unit.rite),
                    office=None,
                    office_position=None,
                    canonical_id=unit.canonical_id,
                    raw_metadata=unit.raw_metadata,
                )
                embed_kwargs: dict[str, Any] = {}
                if _embedder is not None:
                    embed_kwargs["_embedder"] = _embedder
                embed_document(doc_id, unit.body_text, conn, **embed_kwargs)
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"collect {unit.feast_slug}/{unit.rite}: {exc!r}"
            logger.error("Error ingesting %s", msg)
            errors.append(msg)

    return inserted, skipped, errors


def _ingest_daily_office(
    source_root: Path,
    conn: sqlite3.Connection,
    dry_run: bool,
    _embedder: Any,
) -> tuple[int, int, list[str]]:
    """Ingest BCP 1979 Daily Office. Returns (inserted, skipped, errors)."""
    from commonplace_server.liturgical_parsers.bcp_daily_office import parse_daily_office_file

    office_dir = source_root / "DailyOffice"
    if not office_dir.exists():
        office_dir = source_root / "daily_office"
    if not office_dir.exists():
        msg = f"Daily Office directory not found under {source_root}"
        logger.warning(msg)
        return 0, 0, [msg]

    units = []
    for html_file in sorted(office_dir.glob("*.html")):
        units.extend(parse_daily_office_file(html_file))

    if dry_run:
        return len(units), 0, []

    from commonplace_server.pipeline import embed_document

    inserted = skipped = 0
    errors: list[str] = []

    for unit in units:
        try:
            # Disambiguate by slug + rite (slug is already unique enough,
            # but a rite suffix ensures rite_i vs rite_ii don't collide
            # for canticles listed in both mp1/mp2)
            rite_suffix = f"__{unit.rite}" if unit.rite not in ("none", "both") else ""
            source_id = f"{unit.slug}{rite_suffix}"
            source_uri = f"bcp1979://daily_office/{unit.office}/{unit.slug}"
            doc_id, was_inserted = _upsert_document(
                conn,
                source_id=source_id,
                source_uri=source_uri,
                title=unit.name,
                content_hash=_sha256_text(unit.body_text),
            )
            if was_inserted:
                _upsert_meta(
                    conn,
                    document_id=doc_id,
                    category="liturgical_proper",
                    genre=unit.kind,
                    tradition="anglican",
                    source="bcp_1979",
                    language_register=_normalise_language_register(unit.rite),
                    office=_normalise_office(unit.office),
                    office_position=None,
                    canonical_id=unit.canonical_id,
                    raw_metadata=_raw_meta_to_str(unit.raw_metadata),
                )
                embed_kwargs: dict[str, Any] = {}
                if _embedder is not None:
                    embed_kwargs["_embedder"] = _embedder
                embed_document(doc_id, unit.body_text, conn, **embed_kwargs)
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"daily_office {unit.slug}: {exc!r}"
            logger.error("Error ingesting %s", msg)
            errors.append(msg)

    return inserted, skipped, errors


def _psalm_body_text(psalm: Any) -> str:
    """Build a flat body-text string for a parsed psalm by joining all verses."""
    lines: list[str] = []
    # Track pending subheadings
    subheading_map = {sh.before_verse: sh.text for sh in psalm.subheadings}
    for verse in psalm.verses:
        if verse.number in subheading_map:
            lines.append(subheading_map[verse.number])
        lines.append(f"{verse.number} {verse.text}")
    return "\n".join(lines)


def _ingest_psalter(
    source_root: Path,
    conn: sqlite3.Connection,
    dry_run: bool,
    _embedder: Any,
) -> tuple[int, int, list[str]]:
    """Ingest BCP 1979 Psalter. Returns (inserted, skipped, errors)."""
    from commonplace_server.liturgical_parsers.bcp_psalter import parse_psalter_file

    psalter_dir = source_root / "Psalter"
    if not psalter_dir.exists():
        psalter_dir = source_root / "psalter"
    if not psalter_dir.exists():
        msg = f"Psalter directory not found under {source_root}"
        logger.warning(msg)
        return 0, 0, [msg]

    psalms = []
    for html_file in sorted(psalter_dir.glob("*.html")):
        psalms.extend(parse_psalter_file(html_file))

    if dry_run:
        return len(psalms), 0, []

    from commonplace_server.pipeline import embed_document

    inserted = skipped = 0
    errors: list[str] = []

    for psalm in psalms:
        try:
            body_text = _psalm_body_text(psalm)
            if not body_text.strip():
                continue
            source_id = psalm.slug
            source_uri = f"bcp1979://psalter/{psalm.slug}"
            doc_id, was_inserted = _upsert_document(
                conn,
                source_id=source_id,
                source_uri=source_uri,
                title=psalm.title,
                content_hash=_sha256_text(body_text),
            )
            if was_inserted:
                meta_dict: dict[str, Any] = dict(psalm.raw_metadata)
                meta_dict["book"] = psalm.book
                meta_dict["number"] = psalm.number
                meta_dict["source_file"] = psalm.source_file
                if psalm.latin_incipit:
                    meta_dict["latin_incipit"] = psalm.latin_incipit
                _upsert_meta(
                    conn,
                    document_id=doc_id,
                    category="psalter",
                    genre="psalm",
                    tradition="anglican",
                    source="bcp_1979",
                    language_register=None,
                    office=None,
                    office_position=None,
                    canonical_id=psalm.canonical_id,
                    raw_metadata=json.dumps(meta_dict, ensure_ascii=False),
                )
                embed_kwargs: dict[str, Any] = {}
                if _embedder is not None:
                    embed_kwargs["_embedder"] = _embedder
                embed_document(doc_id, body_text, conn, **embed_kwargs)
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"psalm {psalm.slug}: {exc!r}"
            logger.error("Error ingesting %s", msg)
            errors.append(msg)

    return inserted, skipped, errors


def _ingest_proper_liturgies(
    source_root: Path,
    conn: sqlite3.Connection,
    dry_run: bool,
    _embedder: Any,
) -> tuple[int, int, list[str]]:
    """Ingest BCP 1979 Proper Liturgies. Returns (inserted, skipped, errors)."""
    from commonplace_server.liturgical_parsers.bcp_proper_liturgies import (
        parse_proper_liturgies_dir,
    )

    liturgies_dir = source_root / "SpecialDays"
    if not liturgies_dir.exists():
        liturgies_dir = source_root / "proper_liturgies"
    if not liturgies_dir.exists():
        msg = f"Proper Liturgies directory not found under {source_root}"
        logger.warning(msg)
        return 0, 0, [msg]

    units = parse_proper_liturgies_dir(liturgies_dir)

    if dry_run:
        return len(units), 0, []

    from commonplace_server.pipeline import embed_document

    inserted = skipped = 0
    errors: list[str] = []

    # Track a per-liturgy counter per slug to deduplicate repeated slug names
    # within the same liturgy (e.g. multiple "Rubric (Ash Wednesday)" units).
    slug_counters: dict[str, int] = {}

    for unit in units:
        try:
            # Build a unique source_id: slug + section position counter
            # to avoid collisions within the same liturgy.
            base_key = f"{unit.liturgy_slug}__{unit.slug}"
            count = slug_counters.get(base_key, 0)
            slug_counters[base_key] = count + 1
            source_id = f"{base_key}__{count}" if count > 0 else base_key

            source_uri = (
                f"bcp1979://proper_liturgies/{unit.liturgy_slug}/{unit.slug}"
            )
            doc_id, was_inserted = _upsert_document(
                conn,
                source_id=source_id,
                source_uri=source_uri,
                title=unit.name,
                content_hash=_sha256_text(unit.body_text),
            )
            if was_inserted:
                _upsert_meta(
                    conn,
                    document_id=doc_id,
                    category="liturgical_proper",
                    # Normalise kind: "prayer-body" → "prayer_body",
                    # "speaker-line" → "speaker_line",
                    # "psalm-verse" → "psalm_verse"
                    genre=unit.kind.replace("-", "_"),
                    tradition="anglican",
                    source="bcp_1979",
                    language_register=None,
                    office=None,
                    office_position=unit.section if unit.section else None,
                    canonical_id=unit.slug,
                    raw_metadata=_raw_meta_to_str(unit.raw_metadata),
                )
                embed_kwargs: dict[str, Any] = {}
                if _embedder is not None:
                    embed_kwargs["_embedder"] = _embedder
                embed_document(doc_id, unit.body_text, conn, **embed_kwargs)
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"proper_liturgy {unit.slug}: {exc!r}"
            logger.error("Error ingesting %s", msg)
            errors.append(msg)

    return inserted, skipped, errors


def _ingest_prayers_and_thanksgivings(
    source_root: Path,
    conn: sqlite3.Connection,
    dry_run: bool,
    _embedder: Any,
) -> tuple[int, int, list[str]]:
    """Ingest BCP 1979 Prayers and Thanksgivings. Returns (inserted, skipped, errors)."""
    from commonplace_server.liturgical_parsers.bcp_prayers_and_thanksgivings import (
        parse_prayers_and_thanksgivings,
    )

    misc_dir = source_root / "Misc"
    if not misc_dir.exists():
        misc_dir = source_root / "prayers_and_thanksgivings"
    if not misc_dir.exists():
        msg = f"Prayers & Thanksgivings directory not found under {source_root}"
        logger.warning(msg)
        return 0, 0, [msg]

    prayers_path = misc_dir / "Prayers.html"
    thanks_path = misc_dir / "Thanksgivings.html"
    if not prayers_path.exists() or not thanks_path.exists():
        msg = f"Prayers.html or Thanksgivings.html not found in {misc_dir}"
        logger.warning(msg)
        return 0, 0, [msg]

    units = parse_prayers_and_thanksgivings(prayers_path, thanks_path)

    if dry_run:
        return len(units), 0, []

    from commonplace_server.pipeline import embed_document

    inserted = skipped = 0
    errors: list[str] = []

    for unit in units:
        try:
            source_id = unit.slug
            source_uri = f"bcp1979://prayers_and_thanksgivings/{unit.slug}"
            doc_id, was_inserted = _upsert_document(
                conn,
                source_id=source_id,
                source_uri=source_uri,
                title=unit.title,
                content_hash=_sha256_text(unit.body_text),
            )
            if was_inserted:
                _upsert_meta(
                    conn,
                    document_id=doc_id,
                    category="devotional_manual",
                    genre=unit.genre,  # "prayer" or "thanksgiving"
                    tradition="anglican",
                    source="bcp_1979",
                    language_register=None,
                    office=None,
                    office_position=None,
                    canonical_id=unit.canonical_id,
                    raw_metadata=unit.raw_metadata,
                )
                embed_kwargs: dict[str, Any] = {}
                if _embedder is not None:
                    embed_kwargs["_embedder"] = _embedder
                embed_document(doc_id, unit.body_text, conn, **embed_kwargs)
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"prayer/thanksgiving {unit.slug}: {exc!r}"
            logger.error("Error ingesting %s", msg)
            errors.append(msg)

    return inserted, skipped, errors


# ---------------------------------------------------------------------------
# Parser dispatch table
# ---------------------------------------------------------------------------

_PARSER_FN = {
    "collects": _ingest_collects,
    "daily_office": _ingest_daily_office,
    "psalter": _ingest_psalter,
    "proper_liturgies": _ingest_proper_liturgies,
    "prayers_and_thanksgivings": _ingest_prayers_and_thanksgivings,
}


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_liturgy_bcp_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_liturgy_bcp' jobs.

    Parameters
    ----------
    payload:
        ``source_root`` (str, default ~/commonplace/cache/bcp_1979/www.bcponline.org/)
            — directory where the BCP 1979 HTML cache lives.  Tests may point
            this at ``tests/fixtures/bcp_1979/``.
        ``parsers`` (list[str], default all five)
            — subset of ``["collects", "daily_office", "psalter",
               "proper_liturgies", "prayers_and_thanksgivings"]``.
        ``dry_run`` (bool, default False)
            — if True, parse and count units without writing any rows.
    conn:
        Open SQLite connection with migrations applied.
    _embedder:
        Optional embedder override forwarded to ``pipeline.embed_document``
        (used by tests to avoid Ollama calls).

    Returns
    -------
    dict with keys:
        ``parsers_run``, ``units_inserted``, ``units_skipped``, ``errors``.
    """
    t0 = time.monotonic()

    source_root_str: str = payload.get("source_root", DEFAULT_SOURCE_ROOT)
    source_root = Path(source_root_str).expanduser()

    parsers_requested: list[str] = payload.get("parsers", ALL_PARSERS)
    dry_run: bool = bool(payload.get("dry_run", False))

    # Validate requested parsers
    unknown = [p for p in parsers_requested if p not in _PARSER_FN]
    if unknown:
        raise ValueError(f"Unknown parsers: {unknown!r}. Valid: {ALL_PARSERS}")

    total_inserted = 0
    total_skipped = 0
    all_errors: list[str] = []
    parsers_run: list[str] = []

    for parser_name in parsers_requested:
        fn = _PARSER_FN[parser_name]
        logger.info("BCP ingest: running parser %r (dry_run=%s)", parser_name, dry_run)
        try:
            ins, skp, errs = fn(source_root, conn, dry_run, _embedder)
        except Exception as exc:  # noqa: BLE001
            msg = f"{parser_name}: {exc!r}"
            logger.error("Parser %r raised: %s", parser_name, exc)
            all_errors.append(msg)
            continue

        total_inserted += ins
        total_skipped += skp
        all_errors.extend(errs)
        parsers_run.append(parser_name)
        logger.info(
            "BCP ingest: parser=%r inserted=%d skipped=%d errors=%d",
            parser_name,
            ins,
            skp,
            len(errs),
        )

    elapsed_ms = (time.monotonic() - t0) * 1000
    summary: dict[str, Any] = {
        "parsers_run": parsers_run,
        "units_inserted": total_inserted,
        "units_skipped": total_skipped,
        "errors": all_errors,
        "elapsed_ms": elapsed_ms,
        "dry_run": dry_run,
    }
    logger.info(
        "BCP ingest complete: parsers=%r inserted=%d skipped=%d errors=%d elapsed_ms=%.0f",
        parsers_run,
        total_inserted,
        total_skipped,
        len(all_errors),
        elapsed_ms,
    )
    return summary
