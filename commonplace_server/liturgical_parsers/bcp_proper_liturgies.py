"""BCP 1979 Proper Liturgies parser.

Pure function: HTML files in → list[ParsedLiturgyUnit] out.
No I/O beyond file reads, no DB, no network, no global state.

Covers the six proper liturgies published at bcponline.org/SpecialDays/:
  ashwed.html         → Ash Wednesday
  palmsunday.html     → Palm Sunday (The Sunday of the Passion)
  thursday.html       → Maundy Thursday
  friday.html         → Good Friday
  saturday.html       → Holy Saturday
  EasterVigil.html    → The Great Vigil of Easter

Ignored files (ToC / prefatory):
  liturgies.html      — navigation index (< 1 KB)
  concernvigil.html   — prefatory prose, no liturgical units (class="small" only)

Structural notes from real cached HTML (bcponline.org):
- Each file maps to exactly one proper liturgy; the liturgy name derives from
  the page <h1> (or, for Palm Sunday, both <h1> elements joined).
- Block-level traversal is used: top-level children of <body> are processed in
  document order to emit a typed event stream.
- Speaker dialogue lives in borderless <table> elements (class="vrtable" or
  border=0) whose rows have two cells: a speaker cell (class="rubric",
  class="rubrictable", or class="vrpeople") and a line cell.
- Embedded Psalm 51 in Ash Wednesday lives as <table> rows with class="vsnum"
  (verse-number cell) and a content cell — same structure as the Psalter.
- Inline-styled optional blocks in Ash Wednesday use
  ``style="text-indent: 10px; border-left: 2px solid"`` on a <p>; also some
  optional Exsultet paragraphs in EasterVigil.html use
  ``style="border-left: 2px solid"``.  Detected via the ``style`` attribute
  (no class discriminator on these elements).
- <p class="rubric"> paragraphs → kind="rubric".
- <p class="rightfoot"> / <p class="leftfoot"> → page-number markers.
- <p class="small"> / <p class="x-small"> / <p class="smaller"> → skip (citations,
  scripture references, canticle lists).
- <p class="topmenu"> → skip (in-page ToC in EasterVigil.html).
- <h1>, <h2>, <h3>, <h4> tags → section headings; used to emit section boundary
  units and update the current section name.

kind taxonomy (subset of kinds already established in bcp_daily_office):
  prayer-body      — prayer or prose unit (collect, exhortation, absolution, etc.)
  speaker-line     — one turn in a dialogic table (Celebrant / People exchange)
  psalm-verse      — verse from an embedded psalm (Ash Wednesday Psalm 51)
  rubric           — italic rubric / stage direction (<p class="rubric">)

The kind "optional-block" is NOT added; optional blocks in the Ash Wednesday
Imposition of Ashes are flagged via ``is_optional=True`` in raw_metadata but
kept as kind="prayer-body" — no new kind needed.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

KindLiteral = Literal["prayer-body", "speaker-line", "psalm-verse", "rubric"]

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

_NON_ALNUM_UNDERSCORE_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")
_TRADITION_SUFFIX = "anglican"


def _slugify(text: str) -> str:
    """Return ``{name_snake}_anglican`` slug from a liturgy / unit name.

    Replicates ``scripts/feast_import.py::_make_slug`` exactly.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    name_part = _NON_ALNUM_UNDERSCORE_RE.sub("_", text).strip("_")
    return f"{name_part}_{_TRADITION_SUFFIX}"


# ---------------------------------------------------------------------------
# File → liturgy name mapping
# ---------------------------------------------------------------------------

# Files that should short-circuit to an empty list.
_SKIP_FILENAMES: frozenset[str] = frozenset(
    {"liturgies.html", "concernvigil.html"}
)

_FILE_LITURGY_MAP: dict[str, str] = {
    "ashwed.html": "Ash Wednesday",
    "palmsunday.html": "Palm Sunday",
    "thursday.html": "Maundy Thursday",
    "friday.html": "Good Friday",
    "saturday.html": "Holy Saturday",
    "EasterVigil.html": "The Great Vigil of Easter",
}


def _liturgy_name_from_file(source_file: str) -> str | None:
    """Return the canonical liturgy name for a source filename, or None."""
    basename = Path(source_file).name
    return _FILE_LITURGY_MAP.get(basename)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedLiturgyUnit:
    """A single parsed unit from a BCP 1979 Proper Liturgy HTML file."""

    slug: str
    """Canonical ``{name_snake}_anglican`` slug derived from the unit heading."""

    name: str
    """Human-readable heading / label for this unit."""

    liturgy_name: str
    """The proper liturgy this unit belongs to (e.g. 'Ash Wednesday')."""

    liturgy_slug: str
    """Canonical slug of the parent liturgy (e.g. 'ash_wednesday_anglican')."""

    kind: KindLiteral
    """Semantic kind: prayer-body | speaker-line | psalm-verse | rubric."""

    body_text: str
    """Cleaned prose text; <br/> → space; whitespace normalised.
    For psalm-verse, this is the verse text with internal newlines preserved."""

    section: str
    """The most recently seen <h2>/<h3>/<h4> section heading, or the liturgy
    name when no sub-heading has been encountered yet."""

    source_file: str
    """Basename of the HTML source file."""

    page_number: int | None
    """Page number from the nearest preceding leftfoot/rightfoot marker."""

    raw_metadata: dict[str, Any]
    """Free-form dict:
    - "liturgy_name": str
    - "liturgy_slug": str
    - "section": str
    - "kind": str
    - "source_file": str
    - "page_number": int | None
    - "speaker": str | None          (speaker-line only)
    - "psalm_number": int | None     (psalm-verse only)
    - "verse_number": int | None     (psalm-verse only)
    - "is_optional": bool            (True for inline-styled optional blocks)
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _css_classes(el: Tag) -> list[str]:
    raw = el.get("class")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _is_page_marker(el: Tag) -> bool:
    return bool({"rightfoot", "leftfoot"} & set(_css_classes(el)))


def _is_rubric_p(el: Tag) -> bool:
    return "rubric" in _css_classes(el)


def _is_skip_class(el: Tag) -> bool:
    css = set(_css_classes(el))
    return bool(css & {"small", "x-small", "smaller", "topmenu"})


def _extract_page_number(el: Tag) -> int | None:
    text = el.get_text(separator=" ")
    match = re.search(r"\b(\d{2,4})\b", text)
    return int(match.group(1)) if match else None


def _paragraph_text(el: Tag) -> str:
    """Flatten an element to plain text; <br/> → space; normalise whitespace."""
    parts: list[str] = []
    for child in el.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append(" ")
            else:
                parts.append(child.get_text(separator=" "))
    raw = "".join(parts)
    return _WS_RE.sub(" ", raw).strip()


def _has_inline_optional_style(el: Tag) -> bool:
    """True for inline-styled optional/indented blocks.

    Ash Wednesday uses ``style="text-indent: 10px; border-left: 2px solid"``
    and EasterVigil uses ``style="border-left: 2px solid"`` for optional
    Exsultet sections.  We detect via the presence of ``border-left`` in the
    style attribute.
    """
    style: str | None = el.get("style")  # type: ignore[assignment]
    if not style:
        return False
    return "border-left" in style


# ---------------------------------------------------------------------------
# Speaker-table helpers
# ---------------------------------------------------------------------------

# These CSS classes appear on the speaker cell in the three table layouts:
#   palmsunday / friday:   class="rubric"  on <td>
#   friday (vrtable):      class="rubric"  on <td>
#   EasterVigil (vrtable): class="vrpeople" or class="rubrictable"
_SPEAKER_CELL_CLASSES: frozenset[str] = frozenset(
    {"rubric", "rubrictable", "vrpeople"}
)


def _is_speaker_table(table: Tag) -> bool:
    """Return True if this <table> looks like a speaker-dialogue table.

    Criteria: the table has at least one <tr> whose first <td> has a
    CSS class in _SPEAKER_CELL_CLASSES, OR the table has border=0/bordercolor
    and at least one non-empty speaker-class td.  We also check for
    class="vrtable" on the table itself.
    """
    tbl_classes = set(_css_classes(table))
    if "vrtable" in tbl_classes:
        return True
    # Fallback: look for a td with a speaker class
    return any(set(_css_classes(td)) & _SPEAKER_CELL_CLASSES for td in table.find_all("td"))


def _extract_speaker_lines(table: Tag) -> list[tuple[str, str]]:
    """Extract (speaker, line) pairs from a speaker-dialogue table.

    Empty-spacer rows (both cells whitespace-only) are skipped.
    """
    pairs: list[tuple[str, str]] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        speaker_raw = cells[0].get_text(separator=" ").strip()
        line_raw = _paragraph_text(cells[1])
        # Skip decorative spacer rows
        if not speaker_raw and not line_raw:
            continue
        # Normalise the &nbsp; that appears in blank speaker cells
        speaker = _WS_RE.sub(" ", speaker_raw).strip()
        pairs.append((speaker, line_raw))
    return pairs


# ---------------------------------------------------------------------------
# Psalm-table helpers (Ash Wednesday Psalm 51)
# ---------------------------------------------------------------------------


def _is_psalm_table(table: Tag) -> bool:
    """True if this table contains psalm verse rows (class="vsnum" cells)."""
    return bool(table.find("td", class_="vsnum"))


def _extract_psalm_verses(table: Tag) -> list[tuple[int, str]]:
    """Extract (verse_number, verse_text) pairs from a Psalter-format table.

    Skips header row (psnum / pslatin span) and empty spacer rows.
    """
    verses: list[tuple[int, str]] = []
    for row in table.find_all("tr"):
        vsnum_td = row.find("td", class_="vsnum")
        if vsnum_td is None:
            continue
        vnum_text = vsnum_td.get_text(strip=True)
        if not vnum_text.isdigit():
            continue
        verse_num = int(vnum_text)
        # Content cell: the td that is NOT vsnum
        content_td: Tag | None = None
        for td in row.find_all("td"):
            cls = _css_classes(td)
            if "vsnum" not in cls:
                content_td = td
                break
        if content_td is None:
            continue
        # Flatten verse text: <br/> → newline
        parts: list[str] = []
        for child in content_td.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                if child.name == "br":
                    parts.append("\n")
                else:
                    parts.append(child.get_text())
        raw = "".join(parts)
        # Clean up indentation and collapse horizontal whitespace per line
        lines = raw.split("\n")
        cleaned: list[str] = []
        for ln in lines:
            ln_clean = re.sub(r"[ \t\xa0]+", " ", ln).strip()
            if ln_clean:
                cleaned.append(ln_clean)
        if not cleaned:
            continue
        verse_text = "\n".join(cleaned)
        verses.append((verse_num, verse_text))
    return verses


# ---------------------------------------------------------------------------
# Public dataclass builder
# ---------------------------------------------------------------------------


@dataclass
class _UnitAccumulator:
    """Mutable state during document traversal."""

    liturgy_name: str
    liturgy_slug: str
    current_section: str
    current_page: int | None = None
    units: list[ParsedLiturgyUnit] = field(default_factory=list)

    def emit(
        self,
        name: str,
        kind: KindLiteral,
        body_text: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> None:
        """Build and append a ParsedLiturgyUnit."""
        if not body_text.strip():
            return
        slug = _slugify(name)
        raw_meta: dict[str, Any] = {
            "liturgy_name": self.liturgy_name,
            "liturgy_slug": self.liturgy_slug,
            "section": self.current_section,
            "kind": kind,
            "source_file": "",  # filled in by caller
            "page_number": self.current_page,
            "speaker": None,
            "psalm_number": None,
            "verse_number": None,
            "is_optional": False,
        }
        if extra_meta:
            raw_meta.update(extra_meta)
        self.units.append(
            ParsedLiturgyUnit(
                slug=slug,
                name=name,
                liturgy_name=self.liturgy_name,
                liturgy_slug=self.liturgy_slug,
                kind=kind,
                body_text=body_text,
                section=self.current_section,
                source_file=raw_meta.get("source_file", ""),
                page_number=self.current_page,
                raw_metadata=raw_meta,
            )
        )


# ---------------------------------------------------------------------------
# Public parse function
# ---------------------------------------------------------------------------


def parse_proper_liturgy_file(path: Path) -> list[ParsedLiturgyUnit]:
    """Parse one cached BCP 1979 Proper Liturgy HTML file.

    Args:
        path: Absolute path to the cached HTML file.

    Returns:
        Ordered list of ParsedLiturgyUnit records; [] for ToC/skip files.
    """
    source_file = path.name
    if source_file in _SKIP_FILENAMES:
        return []

    liturgy_name = _liturgy_name_from_file(source_file)
    if liturgy_name is None:
        logger.warning("Unknown proper liturgy file: %s — skipping", source_file)
        return []

    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.error("Cannot read %s: %s", path, exc)
        return []

    soup = BeautifulSoup(html, "lxml")
    body_el = soup.find("body")
    if body_el is None:
        return []

    liturgy_slug = _slugify(liturgy_name)
    acc = _UnitAccumulator(
        liturgy_name=liturgy_name,
        liturgy_slug=liturgy_slug,
        current_section=liturgy_name,
    )

    # Track which psalm is currently embedded (Ash Wednesday: Psalm 51)
    current_psalm_number: int | None = None

    # Walk all direct children of <body>
    elements = [el for el in body_el.children if isinstance(el, Tag)]

    for el in elements:
        tag_name = el.name

        # ----------------------------------------------------------------
        # hr / br — skip structural dividers
        # ----------------------------------------------------------------
        if tag_name in ("hr", "br"):
            continue

        # ----------------------------------------------------------------
        # Page markers
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_page_marker(el):
            page_num = _extract_page_number(el)
            if page_num is not None:
                acc.current_page = page_num
            continue

        # ----------------------------------------------------------------
        # Skip navigation / citation / prefatory classes
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_skip_class(el):
            continue

        # ----------------------------------------------------------------
        # Section headings — update section, optionally emit boundary unit
        # ----------------------------------------------------------------
        if tag_name in ("h1", "h2", "h3", "h4"):
            heading_text = _WS_RE.sub(" ", el.get_text()).strip()
            if not heading_text:
                continue
            # h1 is the liturgy title — use as the section reset
            if tag_name == "h1":
                # Don't emit a unit for the title; just reset section
                acc.current_section = liturgy_name
            else:
                # h2/h3/h4 are sub-sections of the liturgy
                acc.current_section = heading_text
                # For h2/h3/h4 in Easter Vigil (id= anchors), emit a boundary
                # rubric-style marker so the section is addressable
                el_id: str | None = el.get("id")  # type: ignore[assignment]
                if el_id:
                    acc.emit(
                        name=heading_text,
                        kind="rubric",
                        body_text=heading_text,
                        extra_meta={
                            "source_file": source_file,
                            "source_anchor": el_id,
                        },
                    )
            continue

        # ----------------------------------------------------------------
        # <p class="rubric"> — rubric instruction
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_rubric_p(el):
            rubric_text = _paragraph_text(el)
            if rubric_text:
                acc.emit(
                    name=f"Rubric ({acc.current_section})",
                    kind="rubric",
                    body_text=rubric_text,
                    extra_meta={"source_file": source_file},
                )
            continue

        # ----------------------------------------------------------------
        # Inline-styled optional block — emit as prayer-body, flagged
        # ----------------------------------------------------------------
        if tag_name == "p" and _has_inline_optional_style(el):
            text = _paragraph_text(el)
            if text:
                acc.emit(
                    name=f"Optional Block ({acc.current_section})",
                    kind="prayer-body",
                    body_text=text,
                    extra_meta={
                        "source_file": source_file,
                        "is_optional": True,
                    },
                )
            continue

        # ----------------------------------------------------------------
        # <p> with <strong> — sub-section heading or named block
        # ----------------------------------------------------------------
        if tag_name == "p" and el.find("strong"):
            strong_el = el.find("strong")
            assert strong_el is not None  # guaranteed by the if above
            heading_text = _WS_RE.sub(" ", strong_el.get_text()).strip()
            if heading_text:
                # This paragraph is a named section heading (e.g.
                # "The Solemn Collects", "Anthem 1", "Litany of Penitence").
                # Emit it as a rubric-style boundary marker and update section.
                acc.current_section = heading_text
                acc.emit(
                    name=heading_text,
                    kind="rubric",
                    body_text=heading_text,
                    extra_meta={"source_file": source_file},
                )
            continue

        # ----------------------------------------------------------------
        # <table> — speaker dialogue OR embedded psalm
        # ----------------------------------------------------------------
        if tag_name == "table":
            # Check psalm table first (Ash Wednesday Psalm 51)
            if _is_psalm_table(el):
                # Try to detect the psalm number from a psnum/pshead span
                psnum_span = el.find(class_="psnum")
                if psnum_span is None:
                    psnum_span = el.find(class_="pshead")
                if psnum_span is not None:
                    psnum_text = re.search(r"\d+", psnum_span.get_text())
                    if psnum_text:
                        current_psalm_number = int(psnum_text.group())

                verses = _extract_psalm_verses(el)
                for vnum, vtext in verses:
                    acc.emit(
                        name=f"Psalm {current_psalm_number or '?'} verse {vnum}",
                        kind="psalm-verse",
                        body_text=vtext,
                        extra_meta={
                            "source_file": source_file,
                            "psalm_number": current_psalm_number,
                            "verse_number": vnum,
                        },
                    )
                continue

            # Speaker-dialogue table
            if _is_speaker_table(el):
                lines = _extract_speaker_lines(el)
                for speaker, line_text in lines:
                    if not line_text:
                        continue
                    label = speaker if speaker else "All"
                    acc.emit(
                        name=f"{label} ({acc.current_section})",
                        kind="speaker-line",
                        body_text=line_text,
                        extra_meta={
                            "source_file": source_file,
                            "speaker": label,
                        },
                    )
                continue

            # Generic table — flatten and emit as prayer-body if substantial
            text = _paragraph_text(el)
            if text:
                acc.emit(
                    name=f"Text ({acc.current_section})",
                    kind="prayer-body",
                    body_text=text,
                    extra_meta={"source_file": source_file},
                )
            continue

        # ----------------------------------------------------------------
        # Plain <p> — prayer-body text
        # ----------------------------------------------------------------
        if tag_name == "p":
            text = _paragraph_text(el)
            if not text:
                continue
            acc.emit(
                name=f"Text ({acc.current_section})",
                kind="prayer-body",
                body_text=text,
                extra_meta={"source_file": source_file},
            )

    # Patch source_file into all units (was set inline above, but enforce)
    result: list[ParsedLiturgyUnit] = []
    for u in acc.units:
        # Rebuild with correct source_file if it got left blank
        if u.source_file != source_file:
            meta = dict(u.raw_metadata)
            meta["source_file"] = source_file
            result.append(
                ParsedLiturgyUnit(
                    slug=u.slug,
                    name=u.name,
                    liturgy_name=u.liturgy_name,
                    liturgy_slug=u.liturgy_slug,
                    kind=u.kind,
                    body_text=u.body_text,
                    section=u.section,
                    source_file=source_file,
                    page_number=u.page_number,
                    raw_metadata=meta,
                )
            )
        else:
            result.append(u)
    return result


def parse_proper_liturgies_dir(path: Path) -> list[ParsedLiturgyUnit]:
    """Parse all Proper Liturgy HTML files in a directory.

    Args:
        path: Directory containing cached .html files.

    Returns:
        Concatenated list of ParsedLiturgyUnit from all content files,
        in filename-sorted order.
    """
    results: list[ParsedLiturgyUnit] = []
    for html_file in sorted(path.glob("*.html")):
        units = parse_proper_liturgy_file(html_file)
        results.extend(units)
    return results


# ---------------------------------------------------------------------------
# Convenience: emit as (text, metadata) tuples for the handler layer
# ---------------------------------------------------------------------------


def iter_units_as_records(
    path: Path,
) -> list[tuple[str, dict[str, Any]]]:
    """Yield (body_text, metadata_dict) pairs from a proper liturgy file.

    This is the shape the ``ingest_liturgy_bcp`` handler expects.
    metadata_dict mirrors the fields used by liturgical_unit_meta.
    """
    units = parse_proper_liturgy_file(path)
    records: list[tuple[str, dict[str, Any]]] = []
    for u in units:
        meta: dict[str, Any] = {
            "category": "liturgical_proper",
            "genre": u.kind,
            "tradition": "anglican",
            "source": "bcp_1979",
            "office": "proper_liturgy",
            "office_position": u.section,
            "canonical_id": u.slug,
            "raw_metadata": json.dumps(u.raw_metadata, ensure_ascii=False),
        }
        records.append((u.body_text, meta))
    return records
