"""LFF 2024 liturgical ingest handler.

handle_liturgy_lff_ingest(payload, conn) is the worker handler for the
'ingest_liturgy_lff' job kind.

One job ingests ALL LFF 2024 commemorations from the shipped parser:
  commonplace_server/liturgical_parsers/lff_2024.py

Per-commemoration insertion (§2.3 of docs/liturgical-ingest-plan.md):
  1. Bio text  → documents (content_type='prose')  + commemoration_bio row
  2. Collect Rite I  → documents (content_type='liturgical_unit') + liturgical_unit_meta
  3. Collect Rite II → same shape with language_register='rite_ii'

Lesson refs + preface go into raw_metadata on liturgical_unit_meta rows.

=== embed_text_override decision ===
Collects (typically 60–200 tokens) are passed to the embedder via a composed
string:

    "Collect for {name} ({tradition}, {rite}).\n\n{body}"

This puts the collect in its semantically-correct neighborhood before the
model sees the body text (§2.7, option Y).  chunks.text always stores the
raw display text; only the embedding deviates.

Bio rows are full prose paragraphs (200–600 tokens on average) and embed
naturally — no override applied.

=== Idempotency ===
Relies on the UNIQUE index on (content_type, source_id) (migration 0003).
Re-running is a no-op on already-ingested rows; the INSERT OR IGNORE +
subsequent SELECT restores the document_id for downstream operations.

=== Transactions ===
Per-commemoration transactions (one commit per commemoration) — matches the
batch handler pattern and limits lock contention on large runs.  Periodic
progress logging every 50 commemorations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default fixture path + pinned SHA256 (matches parser constant)
# ---------------------------------------------------------------------------

_DEFAULT_PDF = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "lff_2024.pdf"
)
_PINNED_SHA256 = "5deea4a131b6c90218ae07b92a17f68bfce000a24a04edd989cdb1edc332bfd7"

# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------


class LiturgyLffPayload(TypedDict, total=False):
    source_pdf: str          # default: tests/fixtures/lff_2024.pdf
    expected_sha256: str     # default: pinned constant
    dry_run: bool            # default: False


# ---------------------------------------------------------------------------
# Slug helper (mirrors scripts/feast_import.py::_make_slug)
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _make_slug(primary_name: str, tradition: str = "anglican") -> str:
    """Return stable feast slug: ``{name_snake}_{tradition}``."""
    name = unicodedata.normalize("NFKD", primary_name).encode("ascii", "ignore").decode()
    name = _NON_ALNUM_RE.sub("_", name.lower()).strip("_")
    return f"{name}_{tradition}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return SHA-256 hex digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Feast slug → id map builder
# ---------------------------------------------------------------------------


def _build_feast_slug_map(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {slug: feast_id} for all LFF 2024 feast rows (source='lff_2024').

    Slug is computed from (primary_name, tradition) to mirror how feast_import.py
    seeds the table.  This avoids needing a slug column on the feast table.
    """
    rows = conn.execute(
        "SELECT id, primary_name, tradition FROM feast WHERE source = 'lff_2024'"
    ).fetchall()
    mapping: dict[str, int] = {}
    for row in rows:
        slug = _make_slug(row["primary_name"], row["tradition"])
        mapping[slug] = int(row["id"])
    return mapping


# ---------------------------------------------------------------------------
# Per-commemoration insertion helpers
# ---------------------------------------------------------------------------


def _upsert_document(
    conn: sqlite3.Connection,
    *,
    content_type: str,
    source_uri: str,
    source_id: str,
    title: str,
    author: str,
    content: str,
    content_hash: str,
) -> tuple[int, bool]:
    """INSERT OR IGNORE a document row; return (document_id, was_new).

    Two idempotency paths:
    1. UNIQUE index on (content_type, source_id) — same row re-submitted.
    2. UNIQUE constraint on content_hash — identical text was already ingested
       under a different source_id (e.g. two commemorations sharing a collect
       text, or a re-import with a changed source_id).

    In case 2 the INSERT is silently ignored but the SELECT by source_id returns
    nothing.  We fall back to a SELECT by content_hash and return that row's id
    as-is (was_new=False) so the caller can still insert the sidecar meta row.
    """
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO documents
            (content_type, source_uri, title, author,
             content_hash, source_id, status)
        VALUES (?, ?, ?, ?, ?, ?, 'ingesting')
        """,
        (content_type, source_uri, title, author, content_hash, source_id),
    )
    if cursor.rowcount == 1:
        # Fresh insert — lastrowid is reliable here.
        return int(cursor.lastrowid), True  # type: ignore[arg-type]

    # INSERT was ignored — try the primary lookup path first.
    row = conn.execute(
        "SELECT id FROM documents WHERE content_type = ? AND source_id = ?",
        (content_type, source_id),
    ).fetchone()
    if row is not None:
        return int(row["id"]), False

    # Fall back: content_hash collision (same text already in DB under a
    # different source_id / content_type).  Return the existing row's id.
    row = conn.execute(
        "SELECT id FROM documents WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    if row is not None:
        return int(row["id"]), False

    raise RuntimeError(
        f"INSERT OR IGNORE silently failed and no existing row found "
        f"for content_type={content_type!r} source_id={source_id!r} "
        f"content_hash={content_hash!r}"
    )


def _ingest_bio(
    conn: sqlite3.Connection,
    *,
    slug: str,
    name: str,
    bio_text: str,
    feast_id: int | None,
    _embedder: Any,
) -> bool:
    """Ingest one bio: document + commemoration_bio + embed.

    Returns True if a new document row was inserted.
    """
    from commonplace_server.pipeline import embed_document

    content_hash = _sha256_text(bio_text)
    source_id = slug  # no rite suffix — one bio per figure

    doc_id, was_new = _upsert_document(
        conn,
        content_type="prose",
        source_uri=f"lff2024://commemoration/{slug}",
        source_id=source_id,
        title=name,
        author="The Episcopal Church 2024",
        content=bio_text,
        content_hash=content_hash,
    )

    # Insert commemoration_bio if feast_id is available
    # (feast_id NOT NULL per schema — skip if no feast match)
    if feast_id is not None:
        conn.execute(
            """
            INSERT OR IGNORE INTO commemoration_bio
                (feast_id, document_id, text, source)
            VALUES (?, ?, ?, 'lff_2024')
            """,
            (feast_id, doc_id, bio_text),
        )

    # Embed
    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    embed_document(doc_id, bio_text, conn, **embed_kwargs)

    return was_new


def _ingest_collect(
    conn: sqlite3.Connection,
    *,
    slug: str,
    name: str,
    rite: str,
    collect_text: str,
    feast_id: int | None,
    canonical_id: str,
    raw_metadata_dict: dict[str, Any],
    _embedder: Any,
) -> bool:
    """Ingest one collect: document + liturgical_unit_meta + embed.

    Returns True if a new document row was inserted.
    """
    from commonplace_server.pipeline import embed_document

    content_hash = _sha256_text(collect_text)
    rite_suffix = rite.replace("_", "-")   # e.g. rite-i, rite-ii
    # slug already ends with _anglican (e.g. elizabeth_ann_seton_anglican)
    # so source_id is e.g. elizabeth_ann_seton_anglican_rite-ii
    source_id = f"{slug}_{rite_suffix}"
    title = f"Collect for {name}"
    author = "The Episcopal Church 2024"

    # language_register: map "rite_i" → "rite_i", "rite_ii" → "rite_ii"
    language_register = rite  # already "rite_i" / "rite_ii"

    raw_metadata_with_rite = dict(raw_metadata_dict)
    raw_metadata_with_rite["language_register"] = language_register
    raw_metadata_json = json.dumps(raw_metadata_with_rite, ensure_ascii=False)

    doc_id, was_new = _upsert_document(
        conn,
        content_type="liturgical_unit",
        source_uri=f"lff2024://collect/{slug}",
        source_id=source_id,
        title=title,
        author=author,
        content=collect_text,
        content_hash=content_hash,
    )

    # liturgical_unit_meta — INSERT OR IGNORE (idempotent via PRIMARY KEY)
    conn.execute(
        """
        INSERT OR IGNORE INTO liturgical_unit_meta
            (document_id, category, genre, tradition, source,
             language_register, office, office_position,
             calendar_anchor_id, canonical_id, raw_metadata)
        VALUES (?, 'liturgical_proper', 'collect', 'anglican', 'lff_2024',
                ?, 'eucharist', NULL, ?, ?, ?)
        """,
        (doc_id, language_register, feast_id, canonical_id, raw_metadata_json),
    )

    # Embed with title-prefixed override for semantic context
    def _override(chunk: Any) -> str:
        rite_label = "Rite I" if rite == "rite_i" else "Rite II"
        return f"Collect for {name} (Anglican, {rite_label}).\n\n{chunk.text}"

    embed_kwargs: dict[str, Any] = {"embed_text_override": _override}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    embed_document(doc_id, collect_text, conn, **embed_kwargs)

    return was_new


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_liturgy_lff_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_liturgy_lff' jobs.

    Parameters
    ----------
    payload:
        Optional keys:
        - ``source_pdf``: path to the LFF 2024 PDF (default: tests/fixtures/lff_2024.pdf)
        - ``expected_sha256``: override the pinned SHA256 guard (default: pinned constant)
        - ``dry_run``: if True, parse + validate but do not INSERT (default: False)
    conn:
        Open SQLite connection with migrations applied.
    _embedder:
        Optional embedder override for tests.

    Returns
    -------
    dict with keys:
        commemorations_processed, bios_inserted, bios_skipped_no_feast,
        collects_inserted, errors
    """
    from commonplace_server.liturgical_parsers.lff_2024 import parse_lff_2024

    t0 = time.monotonic()

    # --- Resolve PDF path ---
    pdf_path = Path(payload.get("source_pdf", str(_DEFAULT_PDF)))
    if not pdf_path.exists():
        raise FileNotFoundError(f"LFF 2024 PDF not found: {pdf_path}")

    # --- SHA256 guard ---
    expected_sha256 = payload.get("expected_sha256", _PINNED_SHA256)
    actual_sha256 = _sha256_file(pdf_path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"LFF 2024 PDF SHA256 mismatch.\n"
            f"  Expected: {expected_sha256}\n"
            f"  Actual:   {actual_sha256}\n"
            f"  Path:     {pdf_path}\n"
            "The PDF has changed since this handler was written. "
            "Verify the file and update EXPECTED_SHA256 in the parser if intentional."
        )

    dry_run: bool = bool(payload.get("dry_run", False))

    # --- Parse PDF ---
    logger.info("Parsing LFF 2024 PDF: %s", pdf_path)
    commemorations = parse_lff_2024(pdf_path)
    logger.info("Parsed %d commemorations", len(commemorations))

    if dry_run:
        # Count what we'd insert without touching the DB
        bios_would_insert = sum(1 for c in commemorations if c.bio_text)
        collects_would_insert = sum(len(c.collects) for c in commemorations)
        return {
            "commemorations_processed": len(commemorations),
            "bios_inserted": 0,
            "bios_skipped_no_feast": 0,
            "collects_inserted": 0,
            "dry_run_bios_would_insert": bios_would_insert,
            "dry_run_collects_would_insert": collects_would_insert,
            "errors": [],
        }

    # --- Build feast slug map ---
    feast_slug_map = _build_feast_slug_map(conn)
    logger.info("Feast slug map has %d entries", len(feast_slug_map))

    # --- Per-commemoration insertion ---
    commemorations_processed = 0
    bios_inserted = 0
    bios_skipped_no_feast = 0
    collects_inserted = 0
    errors: list[str] = []

    for idx, c in enumerate(commemorations, 1):
        try:
            with conn:
                # 1. Feast lookup
                feast_id: int | None = feast_slug_map.get(c.feast_slug)
                if feast_id is None:
                    logger.warning(
                        "No feast found for slug %r (commemoration: %r) — "
                        "bio will be skipped; collects inserted with calendar_anchor_id=NULL",
                        c.feast_slug,
                        c.name,
                    )

                # 2. Bio insertion
                if c.bio_text:
                    if feast_id is None:
                        bios_skipped_no_feast += 1
                    else:
                        was_new = _ingest_bio(
                            conn,
                            slug=c.feast_slug,
                            name=c.name,
                            bio_text=c.bio_text,
                            feast_id=feast_id,
                            _embedder=_embedder,
                        )
                        if was_new:
                            bios_inserted += 1

                # 3. Raw metadata dict for collects
                raw_metadata_dict: dict[str, Any] = {
                    "lesson_refs": c.lesson_refs,
                    "preface": c.preface,
                    "page_number": c.page_number,
                }

                # 4. Collect insertion
                for collect_entry in c.collects:
                    was_new = _ingest_collect(
                        conn,
                        slug=c.feast_slug,
                        name=c.name,
                        rite=collect_entry.rite,
                        collect_text=collect_entry.text,
                        feast_id=feast_id,
                        canonical_id=c.canonical_id,
                        raw_metadata_dict=raw_metadata_dict,
                        _embedder=_embedder,
                    )
                    if was_new:
                        collects_inserted += 1

                commemorations_processed += 1

        except Exception as exc:  # noqa: BLE001
            err = f"Error processing commemoration {c.name!r} ({c.feast_slug}): {exc!r}"
            logger.error(err)
            errors.append(err)

        if idx % 50 == 0:
            logger.info(
                "LFF ingest progress: %d/%d commemorations processed "
                "(bios=%d, collects=%d, skipped_no_feast=%d)",
                idx, len(commemorations), bios_inserted, collects_inserted,
                bios_skipped_no_feast,
            )

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "LFF 2024 ingest complete: %d commemorations in %.0fms "
        "(bios=%d, skipped_no_feast=%d, collects=%d, errors=%d)",
        commemorations_processed, elapsed_ms,
        bios_inserted, bios_skipped_no_feast, collects_inserted, len(errors),
    )

    return {
        "commemorations_processed": commemorations_processed,
        "bios_inserted": bios_inserted,
        "bios_skipped_no_feast": bios_skipped_no_feast,
        "collects_inserted": collects_inserted,
        "errors": errors,
    }
