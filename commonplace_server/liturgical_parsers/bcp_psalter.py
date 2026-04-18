"""BCP 1979 Psalter parser.

Pure function: HTML files in → list[ParsedPsalm] out.
No I/O beyond file reads, no DB, no network, no global state.

Structural notes from real HTML (bcponline.org/Psalter/the_psalter.html):
- The entire psalter is a series of <table> elements (one per printed page).
  All <tr> rows are processed in document order.
- A psalm begins with a row whose content <td> contains a <span class="psnum">
  element.  The psalm number is extracted from that span's text (stripping
  trailing \xa0 non-breaking spaces).  Psalm 1 uses the Roman numeral "I";
  all others use Arabic numerals.
- The content <td> may carry an id= attribute (the page anchor, e.g. id="1").
  For psalms 64 and 138 the id is malformed or wrong in the source; we use the
  psnum span text as authoritative and record the raw id in source_anchor.
- Verse rows: the first <td class="vsnum"> holds the verse number (an integer
  as text); the second <td> holds the verse text.  Empty rows (vsnum = "")
  are decorative spacers and are skipped.
- Subheadings (psalm 119's Hebrew-letter sections, plus "Part I/II" in long
  psalms): rows with a <strong> tag but no <span class="psnum"> and no verse
  number in vsnum.  Book headings ("Book One" … "Book Five") also match this
  pattern and are filtered out by name.
- Verse text: <br/> elements become newlines; L<span style="font-size:
  small">ORD</span> is flattened to "LORD"; whitespace-only text is stripped.
- Half-verse asterisk "*" may appear anywhere in the verse text (mid-line
  caesura); PsalmVerse.half_verse_marker is True when it does.
- <td class="psday"> rows carry day-of-month Morning/Evening Prayer labels
  (e.g. "First Day: Morning Prayer").  They are captured in raw_metadata on
  the psalm they immediately precede.
- Latin incipit: <span class="pslatin"> on the psalm header row (or a
  subheading row in psalm 119 and similar).

Files that are NOT psalms:
  concerning_the_psalter.html — prefatory prose, no <span class="psnum">
  psalter_30day.html          — 30-day reading schedule
  psalter.html                — ToC stub (< 2 KB)
These return [] via _is_skip_file().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

Book = Literal["one", "two", "three", "four", "five"]

_BOOK_RANGES: list[tuple[int, int, Book]] = [
    (1, 41, "one"),
    (42, 72, "two"),
    (73, 89, "three"),
    (90, 106, "four"),
    (107, 150, "five"),
]

# Filenames that should short-circuit to empty output.
_SKIP_FILENAMES: frozenset[str] = frozenset(
    {"psalter.html", "concerning_the_psalter.html", "psalter_30day.html"}
)

# Strong-text values that are book division headings, not psalm subheadings.
_BOOK_HEADINGS: frozenset[str] = frozenset(
    {"Book One", "Book Two", "Book Three", "Book Four", "Book Five"}
)


@dataclass(frozen=True)
class PsalmVerse:
    """A single verse within a psalm."""

    number: int
    text: str
    """Cleaned verse text; <br/> → newline; asterisks preserved."""
    half_verse_marker: bool
    """True if '*' (half-verse caesura) appears anywhere in the verse text."""


@dataclass(frozen=True)
class PsalmSubheading:
    """A subheading within a psalm (e.g. Psalm 119's Hebrew-letter sections)."""

    text: str
    """Plain text of the subheading (e.g. 'Aleph', 'Part I')."""
    before_verse: int
    """The verse number this subheading immediately precedes."""


@dataclass(frozen=True)
class ParsedPsalm:
    """A single BCP 1979 psalm, parsed from the cached HTML file."""

    slug: str
    """Canonical: psalm_001_anglican, psalm_023_anglican, … (zero-padded to 3)."""
    number: int
    """Psalm number 1–150."""
    title: str
    """Human-readable: 'Psalm 1', 'Psalm 23', …"""
    latin_incipit: str | None
    """From <span class="pslatin"> on the psalm header row, or None."""
    verses: tuple[PsalmVerse, ...]
    """Ordered tuple of per-verse records."""
    subheadings: tuple[PsalmSubheading, ...]
    """Subheadings with verse-position anchors (populated for Psalm 119 etc.)."""
    book: Book
    """Psalter book derived from psalm number range."""
    canonical_id: str
    """Same as slug."""
    source_file: str
    """Basename of the HTML file this psalm was parsed from."""
    source_anchor: str | None
    """Raw <td id=> when the psalm opener used one (may be None or wrong)."""
    raw_metadata: dict[str, Any]
    """psday markers observed, keyed by 'psday_before_verse_{n}'."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"[ \t]+")
_NON_ALNUM_UNDERSCORE_RE = re.compile(r"[^a-z0-9]+")
_TRADITION_SUFFIX = "anglican"

# Roman numeral "I" is used only for psalm 1.
_ROMAN_I = "I"


def _psalm_slug(number: int) -> str:
    """Return 'psalm_001_anglican', 'psalm_023_anglican', etc."""
    return f"psalm_{number:03d}_{_TRADITION_SUFFIX}"


def _derive_book(number: int) -> Book:
    for lo, hi, name in _BOOK_RANGES:
        if lo <= number <= hi:
            return name
    raise ValueError(f"Psalm number {number} out of range 1–150")


def _psnum_to_int(text: str) -> int | None:
    """Convert psnum span text (possibly 'I' or '42') to int, or None."""
    stripped = text.strip("\xa0").strip()
    if stripped == _ROMAN_I:
        return 1
    if stripped.isdigit():
        return int(stripped)
    return None


def _flatten_verse_cell(td: Tag) -> str:
    """Flatten a verse <td> to plain text.

    Rules:
    - <br/> → newline character
    - L<span style="font-size: small">ORD</span> → LORD (small-caps flatten)
    - Leading/trailing whitespace stripped per line
    - Inline whitespace (tabs, spaces) collapsed to single space per segment
    """
    parts: list[str] = []
    for child in td.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append("\n")
            else:
                # span (including small-caps LORD), em, strong, a — plain text
                parts.append(child.get_text())
    raw = "".join(parts)
    # Collapse horizontal whitespace per line, strip each line
    lines = raw.split("\n")
    cleaned_lines = [_WS_RE.sub(" ", line).strip() for line in lines]
    # Remove leading/trailing empty lines, but preserve internal structure
    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    joined = "\n".join(cleaned_lines)
    # Collapse multiple consecutive newlines (caused by NavigableStrings that
    # start with \n after a <br/> element in the source) into single newlines.
    return re.sub(r"\n{2,}", "\n", joined)


def _flatten_subheading_cell(td: Tag) -> str:
    """Return plain text of a subheading <td> (e.g. 'Aleph', 'Part I')."""
    strong = td.find("strong")
    if strong:
        return _WS_RE.sub(" ", strong.get_text()).strip()
    return _WS_RE.sub(" ", td.get_text()).strip()


def _latin_from_cell(td: Tag) -> str | None:
    """Extract Latin incipit from a <td> containing a pslatin span."""
    pslatin = td.find(class_="pslatin")
    if not pslatin:
        return None
    text = _WS_RE.sub(" ", pslatin.get_text()).strip()
    # Strip trailing nbsp / whitespace
    text = text.rstrip("\xa0").strip()
    return text if text else None


def _is_skip_file(source_file: str) -> bool:
    """Return True for files that should yield [] (not psalms)."""
    return Path(source_file).name in _SKIP_FILENAMES


# ---------------------------------------------------------------------------
# Row classifier helpers
# ---------------------------------------------------------------------------


def _row_psnum(row: Tag) -> int | None:
    """If this row starts a psalm, return the psalm number; else None."""
    psnum_span = row.find(class_="psnum")
    if not psnum_span:
        return None
    return _psnum_to_int(psnum_span.get_text())


def _row_verse_number(row: Tag) -> int | None:
    """If this row is a verse row, return the verse number; else None."""
    vsnum_td = row.find("td", class_="vsnum")
    if not vsnum_td:
        return None
    text = vsnum_td.get_text(strip=True)
    if text.isdigit():
        return int(text)
    return None


def _row_psday(row: Tag) -> str | None:
    """If this row carries a psday marker, return its text; else None."""
    psday_td = row.find("td", class_="psday")
    if not psday_td:
        return None
    text = _WS_RE.sub(" ", psday_td.get_text()).strip()
    return text if text else None


def _row_is_subheading(row: Tag) -> bool:
    """True if this row has a <strong> tag but no psnum span and no verse num."""
    if row.find(class_="psnum"):
        return False
    vsnum_td = row.find("td", class_="vsnum")
    if vsnum_td and vsnum_td.get_text(strip=True).isdigit():
        return False
    return bool(row.find("strong"))


def _row_content_td(row: Tag) -> Tag | None:
    """Return the non-vsnum content <td> of a row, or None."""
    for td in row.find_all("td"):
        cls = td.get("class") or []
        if "vsnum" not in cls:
            return td
    return None


# ---------------------------------------------------------------------------
# Mutable accumulator
# ---------------------------------------------------------------------------


@dataclass
class _PsalmBuilder:
    number: int
    source_anchor: str | None
    latin_incipit: str | None
    verses: list[PsalmVerse]
    subheadings: list[PsalmSubheading]
    pending_psday: str | None  # psday text seen before any verse yet
    raw_metadata: dict[str, Any]
    # Pending subheading text (waiting for the next verse to get before_verse)
    pending_subheading: str | None

    def record_psday(self, text: str, next_verse_num: int | None) -> None:
        """Record a psday marker, associating it with the upcoming verse."""
        if next_verse_num is not None:
            key = f"psday_before_verse_{next_verse_num}"
        else:
            key = f"psday_pending_{len(self.raw_metadata)}"
        self.raw_metadata[key] = text

    def flush_pending_subheading(self, before_verse: int) -> None:
        if self.pending_subheading is not None:
            self.subheadings.append(
                PsalmSubheading(
                    text=self.pending_subheading,
                    before_verse=before_verse,
                )
            )
            self.pending_subheading = None

    def build(self, source_file: str) -> ParsedPsalm:
        num = self.number
        slug = _psalm_slug(num)
        return ParsedPsalm(
            slug=slug,
            number=num,
            title=f"Psalm {num}",
            latin_incipit=self.latin_incipit,
            verses=tuple(self.verses),
            subheadings=tuple(self.subheadings),
            book=_derive_book(num),
            canonical_id=slug,
            source_file=source_file,
            source_anchor=self.source_anchor,
            raw_metadata=self.raw_metadata,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_psalter_file(path: Path) -> list[ParsedPsalm]:
    """Parse one cached BCP 1979 Psalter HTML file.

    Args:
        path: Absolute path to the cached HTML file.

    Returns:
        A list of ParsedPsalm dataclasses, one per psalm, in number order.
        Returns [] for ToC / schedule / prefatory files.
    """
    source_file = path.name
    if _is_skip_file(source_file):
        return []

    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.error("Cannot read %s: %s", path, exc)
        return []

    # lxml handles the malformed HTML in the source file (unclosed tags,
    # mixed quote chars on id= attributes, etc.)
    soup = BeautifulSoup(html, "lxml")

    results: list[ParsedPsalm] = []
    current: _PsalmBuilder | None = None

    # Pending psday text seen between psalms (before the next psalm starts)
    inter_psalm_psday: str | None = None

    def _finalise(builder: _PsalmBuilder) -> None:
        if builder.verses:
            results.append(builder.build(source_file))

    all_rows = soup.find_all("tr")

    for row in all_rows:
        # ----------------------------------------------------------------
        # 1. Does this row start a new psalm?
        # ----------------------------------------------------------------
        psalm_num = _row_psnum(row)
        if psalm_num is not None:
            if current is not None:
                _finalise(current)
            content_td = _row_content_td(row)
            source_anchor: str | None = None
            latin: str | None = None
            if content_td is not None:
                raw_id = content_td.get("id")
                if raw_id:
                    source_anchor = str(raw_id)
                latin = _latin_from_cell(content_td)
            current = _PsalmBuilder(
                number=psalm_num,
                source_anchor=source_anchor,
                latin_incipit=latin,
                verses=[],
                subheadings=[],
                pending_psday=inter_psalm_psday,
                raw_metadata={},
                pending_subheading=None,
            )
            # If we had a psday queued up between psalms, attach it
            if inter_psalm_psday is not None:
                current.raw_metadata["psday_before_verse_1"] = inter_psalm_psday
                inter_psalm_psday = None
            continue

        # ----------------------------------------------------------------
        # 2. psday marker
        # ----------------------------------------------------------------
        psday_text = _row_psday(row)
        if psday_text is not None:
            if current is None:
                # Before any psalm — hold it for the next psalm
                inter_psalm_psday = psday_text
            else:
                # Determine what verse comes next (none yet means verse 1)
                next_vnum = len(current.verses) + 1
                current.raw_metadata[f"psday_before_verse_{next_vnum}"] = psday_text
            continue

        # ----------------------------------------------------------------
        # 3. Subheading row (<strong> without psnum or verse number)
        # ----------------------------------------------------------------
        if _row_is_subheading(row):
            content_td = _row_content_td(row)
            if content_td is None:
                continue
            strong_text = _flatten_subheading_cell(content_td)
            if not strong_text or strong_text in _BOOK_HEADINGS:
                continue
            # Might also carry a second incipit (e.g. psalm 119 subheadings)
            # We store the strong text as the subheading; the pslatin is
            # ignored here (it accompanies the subheading, not the psalm title).
            if current is not None:
                # Flush any previous pending subheading (shouldn't happen, but safe)
                if current.pending_subheading is not None:
                    # No verse number yet — use last verse + 1 as best guess
                    guess = len(current.verses) + 1
                    current.subheadings.append(
                        PsalmSubheading(text=current.pending_subheading, before_verse=guess)
                    )
                current.pending_subheading = strong_text
            continue

        # ----------------------------------------------------------------
        # 4. Verse row
        # ----------------------------------------------------------------
        verse_num = _row_verse_number(row)
        if verse_num is not None and current is not None:
            # Flush any pending subheading now that we know the verse number
            current.flush_pending_subheading(verse_num)

            content_td = _row_content_td(row)
            if content_td is None:
                continue
            text = _flatten_verse_cell(content_td)
            if not text or text == "\xa0":
                continue
            half = "*" in text
            current.verses.append(
                PsalmVerse(number=verse_num, text=text, half_verse_marker=half)
            )
            continue

        # All other rows (empty spacers, page markers, etc.) are silently skipped.

    # Finalise last psalm
    if current is not None:
        _finalise(current)

    results.sort(key=lambda p: p.number)
    return results
