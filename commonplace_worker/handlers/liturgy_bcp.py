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
  5. embed_document(document_id, body_text, conn, embed_text_override=...).

Idempotency is provided by the UNIQUE index on (content_type, source_id)
from migration 0003.  Re-running is a no-op on already-ingested units.

=== embed_text_override (plan §2.7, option Y) ===
Short BCP units (collects ~60-200 tokens, versicles/rubrics/short prayers
~10-80 tokens) lose to prose chunks (300-500 tokens) in the KNN top-10 when
the embedder sees only the raw body.  Per category we compose a structural
prefix that names the theological/liturgical context (feast, office, rite,
season) so that retrieval can reach these units from a prose seed:

  collect              → "Collect for {name} (Anglican, {rite}). ..."
  daily_office         → "{kind_humanized} from {office} (Anglican, {rite})."
  psalter              → "Psalm {N}{ (…)} (Book of Common Prayer Psalter)."
  proper_liturgy       → "{genre_humanized} from the {liturgy_name}"
                         " (Anglican{, rite})."
  prayer_thanksgiving  → "{genre_humanized} — {name} (Book of Common Prayer)."

``chunks.text`` always stores the raw display text.  Only the embedding input
deviates.  Mirrors the pattern already live in ``liturgy_lff.py`` for
collects.

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
# Embed-string composers (plan §2.7, option Y)
#
# These are pure functions with no dependency on sqlite, chunking, or I/O so
# they can be exercised directly from unit tests with literal inputs.  Each
# returns the string to pass to the embedder as structural context, followed
# by two newlines, followed by the raw display text.
# ---------------------------------------------------------------------------


def _humanize_rite(rite: str | None) -> str | None:
    """Map parser rite value to a display label.

    rite_i  → "Rite I"
    rite_ii → "Rite II"
    both / none / None / other → None (caller omits the rite clause)
    """
    if rite == "rite_i":
        return "Rite I"
    if rite == "rite_ii":
        return "Rite II"
    return None


def _humanize_office(office: str | None) -> str | None:
    """Map parser office value to a display label.

    Accepts the full parser vocabulary (not just the schema subset) so that
    canticle / daily_devotions / great_litany embed strings read naturally.
    """
    mapping = {
        "morning_prayer": "Morning Prayer",
        "evening_prayer": "Evening Prayer",
        "compline": "Compline",
        "noonday": "the Noonday Office",
        "daily_devotions": "Daily Devotions",
        "canticle": "the Canticles",
        "great_litany": "the Great Litany",
        "eucharist": "the Holy Eucharist",
    }
    if office in mapping:
        return mapping[office]
    return None


# Kind vocabulary for daily_office + proper_liturgy parsers.  Keys are the
# raw parser values (underscore-separated); values are natural-language forms.
_KIND_HUMAN = {
    "canticle": "Canticle",
    "prayer": "Prayer",
    "prayer_body": "Prayer",
    "creed": "Creed",
    "psalm_ref": "Psalm reference",
    "psalm_verse": "Psalm verse",
    "seasonal_sentence": "Seasonal sentence",
    "versicle_response": "Versicle and response",
    "rubric_block": "Rubric",
    "rubric": "Rubric",
    "intro": "Introduction",
    "suffrage": "Suffrage",
    "speaker_line": "Liturgical response",
    "collect": "Collect",
    "thanksgiving": "Thanksgiving",
}


def _humanize_kind(kind: str | None) -> str:
    """Map parser kind/genre value to a natural-language label.

    Unknown values fall back to a title-cased version of the raw string with
    underscores replaced by spaces.  Never returns an empty string.
    """
    if kind is None or kind == "":
        return "Liturgical unit"
    if kind in _KIND_HUMAN:
        return _KIND_HUMAN[kind]
    return kind.replace("_", " ").replace("-", " ").strip().capitalize() or "Liturgical unit"


def compose_collect_embed(
    *,
    name: str,
    rite: str | None,
    section: str | None,
    body_text: str,
) -> str:
    """Compose the embed string for a BCP collect.

    Shape (matches the LFF 2024 handler pattern):
        "Collect for {name} (Anglican, Rite I/II). Propers for {section}.\n\n{body}"

    ``section`` is the parser's filename-derived label (seasons / holydays /
    common / various) and is folded into a short trailing clause so the
    embedding sees either a feast/season anchor or a context tag.  If the
    rite is not rite_i/rite_ii the rite clause is omitted.
    """
    rite_label = _humanize_rite(rite)
    paren_parts: list[str] = ["Anglican"]
    if rite_label is not None:
        paren_parts.append(rite_label)
    header = f"Collect for {name} ({', '.join(paren_parts)})."

    if section:
        section_label = section.replace("_", " ").strip()
        header = f"{header} Propers for {section_label}."

    return f"{header}\n\n{body_text}"


def compose_daily_office_embed(
    *,
    name: str,
    kind: str | None,
    office: str | None,
    rite: str | None,
    body_text: str,
) -> str:
    """Compose the embed string for a BCP Daily Office unit.

    Shape:
        "{kind_humanized} '{name}' from {office_humanized}
         (Anglican{, rite}).\n\n{body}"

    ``office`` and ``rite`` may be None / "both" / "none" — in those cases
    the corresponding clause is dropped.  Name is quoted only when present
    and distinct from the kind label.
    """
    kind_label = _humanize_kind(kind)
    office_label = _humanize_office(office)

    # Only quote the name if it adds information beyond the kind label.
    show_name = bool(name and name.lower() != kind_label.lower())

    lead = kind_label
    if show_name:
        lead = f'{kind_label} "{name}"'

    if office_label is not None:
        lead = f"{lead} from {office_label}"

    paren_parts: list[str] = ["Anglican"]
    rite_label = _humanize_rite(rite)
    if rite_label is not None:
        paren_parts.append(rite_label)
    header = f"{lead} ({', '.join(paren_parts)})."

    return f"{header}\n\n{body_text}"


def compose_psalter_embed(
    *,
    number: int,
    title: str | None,
    latin_incipit: str | None,
    body_text: str,
) -> str:
    """Compose the embed string for a BCP psalter unit.

    Shape:
        "Psalm {N}{ — {latin_incipit}} (Book of Common Prayer Psalter).\n\n{body}"

    Note: the current BCP ingest handler stores one document per psalm (all
    verses flattened into ``body_text``), not one per verse.  The embed
    string reflects that doc shape; a future per-verse handler can add a
    distinct composer keyed on verse number.
    """
    header = f"Psalm {number}"
    if latin_incipit:
        header = f"{header} — {latin_incipit}"
    elif title and title.strip() and title.strip().lower() != f"psalm {number}".lower():
        header = f"{header} ({title.strip()})"
    header = f"{header} (Book of Common Prayer Psalter)."
    return f"{header}\n\n{body_text}"


def compose_proper_liturgy_embed(
    *,
    name: str,
    kind: str | None,
    liturgy_name: str | None,
    section: str | None,
    body_text: str,
) -> str:
    """Compose the embed string for a BCP Proper Liturgy unit.

    Shape:
        "{kind_humanized}{ '{name}'} from the {liturgy_name}{ — {section}}
         (Anglican).\n\n{body}"

    Proper liturgies (Ash Wednesday, Palm Sunday, etc.) are Rite II by
    convention in the 1979 book — we do not emit a rite clause since the
    parser does not carry one.
    """
    kind_label = _humanize_kind(kind)
    show_name = bool(name and name.lower() != kind_label.lower())
    lead = kind_label
    if show_name:
        lead = f'{kind_label} "{name}"'

    if liturgy_name:
        lead = f"{lead} from the {liturgy_name}"

    if section and liturgy_name and section.strip().lower() != liturgy_name.strip().lower():
        lead = f"{lead} — {section.strip()}"

    header = f"{lead} (Anglican)."
    return f"{header}\n\n{body_text}"


def compose_prayer_thanksgiving_embed(
    *,
    title: str,
    genre: str | None,
    section_header: str | None,
    body_text: str,
) -> str:
    """Compose the embed string for a BCP Prayer or Thanksgiving.

    Shape:
        "{genre_humanized} — {title}{ ({section_header})}
         (Book of Common Prayer).\n\n{body}"
    """
    genre_label = _humanize_kind(genre)
    header = f"{genre_label} — {title}"
    if section_header and section_header.strip():
        header = f"{header} ({section_header.strip()})"
    header = f"{header} (Book of Common Prayer)."
    return f"{header}\n\n{body_text}"


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

                def _collect_override(
                    chunk: Any,
                    *,
                    _name: str = unit.feast_name,
                    _rite: str = unit.rite,
                    _section: str = unit.section,
                ) -> str:
                    return compose_collect_embed(
                        name=_name,
                        rite=_rite,
                        section=_section,
                        body_text=chunk.text,
                    )

                embed_kwargs: dict[str, Any] = {"embed_text_override": _collect_override}
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

                def _office_override(
                    chunk: Any,
                    *,
                    _name: str = unit.name,
                    _kind: str = unit.kind,
                    _office: str = unit.office,
                    _rite: str = unit.rite,
                ) -> str:
                    return compose_daily_office_embed(
                        name=_name,
                        kind=_kind,
                        office=_office,
                        rite=_rite,
                        body_text=chunk.text,
                    )

                embed_kwargs: dict[str, Any] = {"embed_text_override": _office_override}
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

                def _psalm_override(
                    chunk: Any,
                    *,
                    _number: int = psalm.number,
                    _title: str = psalm.title,
                    _latin: str | None = psalm.latin_incipit,
                ) -> str:
                    return compose_psalter_embed(
                        number=_number,
                        title=_title,
                        latin_incipit=_latin,
                        body_text=chunk.text,
                    )

                embed_kwargs: dict[str, Any] = {"embed_text_override": _psalm_override}
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

                def _proper_override(
                    chunk: Any,
                    *,
                    _name: str = unit.name,
                    _kind: str = unit.kind.replace("-", "_"),
                    _liturgy_name: str = unit.liturgy_name,
                    _section: str = unit.section,
                ) -> str:
                    return compose_proper_liturgy_embed(
                        name=_name,
                        kind=_kind,
                        liturgy_name=_liturgy_name,
                        section=_section,
                        body_text=chunk.text,
                    )

                embed_kwargs: dict[str, Any] = {"embed_text_override": _proper_override}
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

                def _pt_override(
                    chunk: Any,
                    *,
                    _title: str = unit.title,
                    _genre: str = unit.genre,
                    _section_header: str = unit.section_header,
                ) -> str:
                    return compose_prayer_thanksgiving_embed(
                        title=_title,
                        genre=_genre,
                        section_header=_section_header,
                        body_text=chunk.text,
                    )

                embed_kwargs: dict[str, Any] = {"embed_text_override": _pt_override}
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
