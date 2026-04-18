"""LFF 2024 PDF parser.

Pure function: PDF path in → list[ParsedCommemoration] out.
No I/O beyond file reads, no DB, no network, no global state.

=== Font signatures identified from lff_2024.pdf (verified pages 30–600) ===

Font                     Size   Semantic role
----                     ----   -------------
SabonLTStd-Bold          17.0pt Commemoration name (entry boundary).
                                 Names may wrap across multiple consecutive spans.
                                 Bracketed entries (e.g. "[Lili'uokalani of Hawai'i]")
                                 are trial-use commemorations; brackets are preserved in
                                 the name field and flagged via trial_use=True.
SabonLTStd-Italic        11.0pt Date header (e.g. "January 1") — appears at top of every
                                 page in that entry's date.  Also used for:
                                 • "Amen." (collect terminator)
                                 • Preface line (e.g. "Preface of the Incarnation")
SabonLTStd-Italic         9.0pt • Subtitle / rank (e.g. "Vowed Religious and Educator, 1821")
                                   — appears immediately after the Bold-17 name spans
                                 • Italic 9pt in bio prose = book/journal titles or rubric notes
                                 • Alternate reading ("or") in lesson refs
                                 • Date footnote at bottom of collect page (italic 11pt "January 29"
                                   for out-of-calendar entries)
SabonLTStd               11.0pt • Page number (first span on page, a bare integer string)
                                 • Collect body text
                                 • "I" or "II\t..." — rite indicator lines
SabonLTStd                9.0pt Bio prose (body of biographical note)
                                 Also lesson citation lines after "Lessons and Psalm"
SabonLTStd-Bold           9.0pt "Lessons and Psalm" label
SabonLTStd                9.7pt Ligature/special-char variant of 9.0pt bio (treat as body prose)

=== Page layout (each commemoration spans two pages) ===

Bio page (odd position in entry pair):
  Page number (SabonLTStd 11pt bare int)
  Date header (SabonLTStd-Italic 11pt)
  [optional italic 9pt rubric note at bottom]
  Body prose spans (SabonLTStd 9pt + SabonLTStd-Italic 9pt for titles)
  [italic 11pt date footnote at very bottom for alternate-date entries]

Collect page (even position in entry pair):
  Page number (SabonLTStd 11pt bare int)
  Date header (SabonLTStd-Italic 11pt)
  Name (SabonLTStd-Bold 17pt — one or more spans)
  [Subtitle (SabonLTStd-Italic 9pt)]
  "I" line (SabonLTStd 11pt = "I")  → Rite I collect body follows
  Collect body (SabonLTStd 11pt lines)
  "Amen." (SabonLTStd-Italic 11pt)
  "II\t..." line (SabonLTStd 11pt starting with "II") → Rite II collect body
  Collect body (SabonLTStd 11pt lines)
  "Amen." (SabonLTStd-Italic 11pt)
  "Lessons and Psalm" (SabonLTStd-Bold 9pt)
  Lesson citations (SabonLTStd 9pt)
  Preface line (SabonLTStd-Italic 11pt — last 11pt italic before next entry)
  [out-of-calendar italic 11pt date at very bottom]

=== State machine ===

For each page, the parser classifies it as a BIO page or COLLECT page.
A new commemoration boundary is signalled by SabonLTStd-Bold 17pt.
The machine collects: date, name, trial_use, subtitle, bio_text,
rite_i collect, rite_ii collect, lesson_refs, preface, page_number.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Font constants (verified against lff_2024.pdf)
# ---------------------------------------------------------------------------

_FONT_BOLD = "SabonLTStd-Bold"
_FONT_ITALIC = "SabonLTStd-Italic"
_FONT_ROMAN = "SabonLTStd"
# 9.7pt is a ligature variant seen in bio prose (treated as body)
_SIZE_NAME = 17.0
_SIZE_BODY = 11.0
_SIZE_BIO = 9.0
_SIZE_BIO_ALT = 9.7  # ligature variant
_SIZE_LABEL = 9.0

_TOL = 0.6  # size comparison tolerance

# ---------------------------------------------------------------------------
# Slug helper (mirrors scripts/feast_import.py::_make_slug)
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_TRADITION = "anglican"


def _make_slug(name: str) -> str:
    """Return canonical feast slug: {name_snake}_anglican."""
    name_norm = unicodedata.normalize("NFKD", name)
    name_ascii = name_norm.encode("ascii", "ignore").decode("ascii")
    name_lower = name_ascii.lower()
    # Strip brackets for trial-use entries
    name_clean = re.sub(r"[\[\]]", "", name_lower)
    name_part = _NON_ALNUM_RE.sub("_", name_clean).strip("_")
    return f"{name_part}_{_TRADITION}"


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCollectEntry:
    """One rite's collect within a commemoration."""

    rite: str  # "rite_i" or "rite_ii"
    text: str  # Full collect text including "Amen."


@dataclass(frozen=True)
class ParsedCommemoration:
    """A single LFF 2024 commemoration, parsed from the PDF.

    Mirrors the design style of ParsedOffice / ParsedPsalm from the BCP parsers.
    """

    name: str
    """Human-readable commemoration name (whitespace-normalised, brackets preserved
    for trial-use entries)."""

    date: str
    """Calendar date as it appears in the PDF (e.g. "January 4", "July 29")."""

    feast_slug: str
    """Canonical `{name_snake}_anglican` slug per _make_slug()."""

    canonical_id: str
    """Same as feast_slug (for LFF entries the slug IS the canonical id)."""

    subtitle: str
    """Role / rank / date line (e.g. "Vowed Religious and Educator, 1821"),
    or empty string if absent."""

    bio_text: str
    """Full biographical note as plain text (whitespace-normalised).
    May be empty if only a collect page was found (edge case)."""

    collects: list[ParsedCollectEntry]
    """Ordered list of collects — typically [rite_i, rite_ii]."""

    lesson_refs: list[str]
    """Scripture / psalm citations in order after "Lessons and Psalm"."""

    preface: str
    """Preface line (e.g. "Preface of the Incarnation"), or empty string."""

    trial_use: bool
    """True if the name is surrounded by brackets (trial-use commemoration)."""

    page_number: int | None
    """PDF page number (1-indexed) of the collect page."""

    tradition: str = "anglican"
    source: str = "lff_2024"
    genre: str = "collect"
    category: str = "hagiography"

    raw_metadata: str = ""
    """JSON string with: page_number, bio_page_number, date, preface,
    trial_use, source."""


# ---------------------------------------------------------------------------
# Internal span helper
# ---------------------------------------------------------------------------

@dataclass
class _Span:
    font: str
    size: float
    text: str

    def is_bold_name(self) -> bool:
        return self.font == _FONT_BOLD and abs(self.size - _SIZE_NAME) < _TOL

    def is_date_header(self) -> bool:
        """Italic 11pt — date header or preface or Amen."""
        return self.font == _FONT_ITALIC and abs(self.size - _SIZE_BODY) < _TOL

    def is_body_text(self) -> bool:
        """Roman 11pt — page number, collect body, rite labels."""
        return self.font == _FONT_ROMAN and abs(self.size - _SIZE_BODY) < _TOL

    def is_bio_prose(self) -> bool:
        """9pt text (roman or italic) — biographical note or lesson refs."""
        return abs(self.size - _SIZE_BIO) < _TOL or abs(self.size - _SIZE_BIO_ALT) < _TOL

    def is_lessons_label(self) -> bool:
        return self.font == _FONT_BOLD and abs(self.size - _SIZE_LABEL) < _TOL

    def is_italic_9pt(self) -> bool:
        return self.font == _FONT_ITALIC and (
            abs(self.size - _SIZE_BIO) < _TOL or abs(self.size - _SIZE_BIO_ALT) < _TOL
        )


def _page_spans(page: Any) -> list[_Span]:
    """Extract all non-empty text spans from a PyMuPDF page as _Span objects."""
    spans: list[_Span] = []
    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                # Preserve whitespace-normalised non-empty spans
                if text.strip():
                    spans.append(
                        _Span(
                            font=span.get("font", ""),
                            size=round(span.get("size", 0), 1),
                            text=text,
                        )
                    )
    return spans


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")

_DATE_MONTH_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}$"
)


def _is_date(text: str) -> bool:
    return bool(_DATE_MONTH_RE.match(text.strip()))


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


@dataclass
class _CommemorationBuilder:
    """Mutable accumulator for one commemoration being assembled."""

    name_parts: list[str] = field(default_factory=list)
    date: str = ""
    subtitle: str = ""
    bio_parts: list[str] = field(default_factory=list)
    rite_i_parts: list[str] = field(default_factory=list)
    rite_ii_parts: list[str] = field(default_factory=list)
    lesson_refs: list[str] = field(default_factory=list)
    preface: str = ""
    bio_page: int | None = None
    collect_page: int | None = None

    @property
    def name(self) -> str:
        return _clean(" ".join(self.name_parts))

    @property
    def trial_use(self) -> bool:
        n = self.name
        return n.startswith("[") and n.endswith("]")

    def build(self) -> ParsedCommemoration | None:
        name = self.name
        if not name:
            return None
        feast_slug = _make_slug(name)
        rite_i_text = _clean(" ".join(self.rite_i_parts))
        rite_ii_text = _clean(" ".join(self.rite_ii_parts))
        collects: list[ParsedCollectEntry] = []
        if rite_i_text:
            collects.append(ParsedCollectEntry(rite="rite_i", text=rite_i_text))
        if rite_ii_text:
            collects.append(ParsedCollectEntry(rite="rite_ii", text=rite_ii_text))

        raw_meta = json.dumps(
            {
                "page_number": self.collect_page,
                "bio_page_number": self.bio_page,
                "date": self.date,
                "preface": self.preface,
                "trial_use": self.trial_use,
                "source": "lff_2024",
            },
            ensure_ascii=False,
        )

        return ParsedCommemoration(
            name=name,
            date=self.date,
            feast_slug=feast_slug,
            canonical_id=feast_slug,
            subtitle=self.subtitle,
            bio_text=_clean(" ".join(self.bio_parts)),
            collects=collects,
            lesson_refs=self.lesson_refs,
            preface=self.preface,
            trial_use=self.trial_use,
            page_number=self.collect_page,
            raw_metadata=raw_meta,
        )


# ---------------------------------------------------------------------------
# Per-page classify and parse helpers
# ---------------------------------------------------------------------------

# State values for the collect-page state machine
_ST_BEFORE_NAME = "before_name"
_ST_IN_NAME = "in_name"
_ST_AFTER_NAME = "after_name"   # awaiting subtitle or rite-I start
_ST_RITE_I = "rite_i"
_ST_AFTER_RITE_I = "after_rite_i"
_ST_RITE_II = "rite_ii"
_ST_AFTER_RITE_II = "after_rite_ii"
_ST_LESSONS = "lessons"
_ST_DONE = "done"


def _parse_collect_page(
    spans: list[_Span],
    page_num: int,
    builder: _CommemorationBuilder,
) -> None:
    """Apply collect-page spans to builder.

    Called when a Bold-17pt name span is detected on this page.
    Advances builder's rite_i/ii, lessons, preface, collect_page fields.
    """
    builder.collect_page = page_num

    state = _ST_BEFORE_NAME
    # We use a manual index to allow multi-pass
    i = 0
    n = len(spans)

    while i < n:
        span = spans[i]
        text = _clean(span.text)

        if state == _ST_BEFORE_NAME:
            if span.is_bold_name():
                builder.name_parts.append(text)
                state = _ST_IN_NAME
            # Skip page number and date header before name
            i += 1
            continue

        if state == _ST_IN_NAME:
            if span.is_bold_name():
                builder.name_parts.append(text)
                i += 1
                continue
            # First non-bold-17 after name
            # Could be subtitle (italic 9pt) or rite-I label (roman 11pt "I")
            if span.is_italic_9pt():
                builder.subtitle = text
                state = _ST_AFTER_NAME
            elif span.is_body_text() and text in ("I", "II"):
                state = _ST_AFTER_NAME
                # Don't advance — re-process in AFTER_NAME
                continue
            else:
                state = _ST_AFTER_NAME
            i += 1
            continue

        if state == _ST_AFTER_NAME:
            # Looking for "I" (Rite I marker) or "I\t..." (Rite I + first body line)
            if span.is_body_text():
                # Check for "I" alone or "I<whitespace>rest-of-line"
                # The raw span text (not _clean()'d) may be "I \t Blessed God..."
                raw_text = span.text
                # Match "I" possibly followed by whitespace + body text
                rite_i_match = re.match(r"^I\s+(.+)$", raw_text, re.DOTALL)
                if rite_i_match:
                    # "I<ws>body" — the whole first body line is here
                    body_part = _clean(rite_i_match.group(1))
                    if body_part:
                        builder.rite_i_parts.append(body_part)
                    state = _ST_RITE_I
                    i += 1
                    continue
                if text == "I":
                    state = _ST_RITE_I
                    i += 1
                    continue
                if text.startswith("II"):
                    # Occasionally Rite I is absent
                    state = _ST_RITE_II
                    # Strip leading "II\t" prefix and add remainder if non-trivial
                    body_part = re.sub(r"^II\s*", "", text).strip()
                    if body_part:
                        builder.rite_ii_parts.append(body_part)
                    i += 1
                    continue
            i += 1
            continue

        if state == _ST_RITE_I:
            if span.is_date_header() and _clean(span.text) == "Amen.":
                builder.rite_i_parts.append("Amen.")
                state = _ST_AFTER_RITE_I
                i += 1
                continue
            if span.is_date_header():
                # It's Amen or a preface/date line; treat as collect body
                builder.rite_i_parts.append(text)
                i += 1
                continue
            if span.is_body_text():
                t = text
                if t.startswith("II"):
                    # Rite II marker embedded without separate Amen? Unusual, but handle
                    state = _ST_RITE_II
                    body_part = re.sub(r"^II\s*", "", t).strip()
                    if body_part:
                        builder.rite_ii_parts.append(body_part)
                    i += 1
                    continue
                builder.rite_i_parts.append(t)
                i += 1
                continue
            i += 1
            continue

        if state == _ST_AFTER_RITE_I:
            # Looking for "II\t..." to start Rite II
            if span.is_body_text() and text.startswith("II"):
                state = _ST_RITE_II
                body_part = re.sub(r"^II\s*", "", text).strip()
                if body_part:
                    builder.rite_ii_parts.append(body_part)
                i += 1
                continue
            if span.is_body_text() and text == "II":
                state = _ST_RITE_II
                i += 1
                continue
            i += 1
            continue

        if state == _ST_RITE_II:
            if span.is_date_header() and _clean(span.text) == "Amen.":
                builder.rite_ii_parts.append("Amen.")
                state = _ST_AFTER_RITE_II
                i += 1
                continue
            if span.is_date_header():
                builder.rite_ii_parts.append(text)
                i += 1
                continue
            if span.is_body_text():
                builder.rite_ii_parts.append(text)
                i += 1
                continue
            if span.is_lessons_label():
                state = _ST_LESSONS
                i += 1
                continue
            i += 1
            continue

        if state == _ST_AFTER_RITE_II:
            if span.is_lessons_label():
                state = _ST_LESSONS
                i += 1
                continue
            i += 1
            continue

        if state == _ST_LESSONS:
            # Lesson citations are 9pt roman; "or" is 9pt italic; preface is italic 11pt
            if span.is_date_header():
                t = text
                if _is_date(t):
                    # Out-of-calendar date footnote at bottom — not a preface
                    i += 1
                    continue
                if t == "Amen.":
                    i += 1
                    continue
                # Preface line (italic 11pt non-date non-Amen)
                if builder.preface:
                    builder.preface += " " + t
                else:
                    builder.preface = t
                i += 1
                continue
            if span.is_bio_prose() or span.is_lessons_label():
                # Skip rite-set labels (italic 9pt single roman numerals "I", "II", "III")
                # that appear in multi-proper entries (e.g. Christmas Day Third Proper)
                if span.is_italic_9pt() and re.fullmatch(r"I{1,3}V?", text):
                    i += 1
                    continue
                if text and text not in ("or",):
                    builder.lesson_refs.append(text)
                elif text == "or" and builder.lesson_refs:
                    # Separator between alternate readings — preserve for clarity
                    builder.lesson_refs.append("or")
                i += 1
                continue
            i += 1
            continue

        i += 1

    # If we accumulated lesson "or" separators without following refs, clean up
    while builder.lesson_refs and builder.lesson_refs[-1] == "or":
        builder.lesson_refs.pop()


def _parse_bio_page(
    spans: list[_Span],
    page_num: int,
    builder: _CommemorationBuilder,
    date_override: str | None = None,
) -> None:
    """Apply bio-page spans to builder.

    Bio pages contain the date header and biographical prose.
    No Bold-17 names here — those live on the collect page.

    Two date patterns:
    1. Normal: italic-11pt date header at top of page.
    2. Alternate-date entries: date appears as italic-11pt at *bottom* of page
       (e.g. "October 20" after a full bio page with no top-date header).
    """
    builder.bio_page = page_num
    bio_started = False

    # Collect all date candidates on this page; we'll use the last one
    # if no top-of-page date is found.
    date_candidates: list[str] = []

    for span in spans:
        text = _clean(span.text)
        if not text:
            continue

        # Page number (first roman 11pt bare integer on the page) — skip
        if span.is_body_text() and re.fullmatch(r"\d+", text):
            continue

        # Date header (italic 11pt date-shaped text)
        if span.is_date_header():
            if _is_date(text):
                date_candidates.append(text)
                if not bio_started:
                    # Top-of-page date header — start bio after this
                    if not builder.date and not date_override:
                        builder.date = text
                    elif date_override:
                        builder.date = date_override
                    bio_started = True
                # Bottom-of-page date footnote doesn't stop bio collection
                continue
            # Non-date italic 11pt — ignore
            continue

        # Bio prose spans (9pt roman or italic, or 8.8pt special)
        if span.is_bio_prose() or (span.font == _FONT_ITALIC and span.is_bio_prose()):
            # Start bio even if we haven't seen a date header yet
            # (handles pages that start directly with prose, date at bottom)
            bio_started = True
            builder.bio_parts.append(text)
            continue

        if span.is_body_text() and bio_started:
            # Sometimes body-text (11pt) appears in a bio footnote — skip
            continue

    # If no date was set from a top header, use the last date candidate
    # (alternate-date footnote at bottom of page)
    if not builder.date and date_candidates:
        builder.date = date_candidates[-1]


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

# Content page range: first proper entry starts at PDF page 30 (0-indexed 29)
# The Commons section starts at PDF page ~601 (0-indexed 600); stop there.
_FIRST_CONTENT_PAGE_0 = 29    # "January 1" bio page
_LAST_CONTENT_PAGE_0 = 599    # last proper entry (Frances Gaudet collect page 597, 0-indexed 596+)


def parse_lff_2024(pdf_path: str | Path) -> list[ParsedCommemoration]:
    """Parse *Lesser Feasts and Fasts 2024* PDF into ParsedCommemoration records.

    Uses a two-pass approach to handle the bio-before-collect page layout:
      Pass 1: Identify page roles (bio vs. collect) and segment into entry pairs.
      Pass 2: Build ParsedCommemoration from each entry pair.

    Args:
        pdf_path: Path to the PDF file (tests/fixtures/lff_2024.pdf).

    Returns:
        Ordered list of ParsedCommemoration, one per commemoration.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError(
            "PyMuPDF (fitz) is required for the LFF 2024 parser. "
            "Install it: pip install pymupdf"
        ) from exc

    pdf = fitz.open(str(pdf_path))
    total_pages = len(pdf)

    # -----------------------------------------------------------------------
    # Pass 1: collect per-page span lists and classify pages
    # -----------------------------------------------------------------------

    @dataclass
    class _PageInfo:
        page_num: int        # 1-indexed
        spans: list[_Span]
        has_new_entry: bool  # True = collect page (has Bold-17pt name)
        date: str            # running date from italic-11pt header on this page

    pages: list[_PageInfo] = []
    running_date = ""

    for page_idx in range(_FIRST_CONTENT_PAGE_0, min(total_pages, _LAST_CONTENT_PAGE_0 + 1)):
        page = pdf[page_idx]
        page_num = page_idx + 1
        spans = _page_spans(page)
        if not spans:
            continue

        has_new_entry = any(s.is_bold_name() for s in spans)

        # Update running date from first italic-11pt date-shaped span
        page_date = running_date
        for s in spans:
            if s.is_date_header() and _is_date(_clean(s.text)):
                running_date = _clean(s.text)
                page_date = running_date
                break

        pages.append(
            _PageInfo(
                page_num=page_num,
                spans=spans,
                has_new_entry=has_new_entry,
                date=page_date,
            )
        )

    pdf.close()

    # -----------------------------------------------------------------------
    # Pass 2: segment pages into (bio_page?, collect_page) pairs and build
    # -----------------------------------------------------------------------
    # Layout observation:
    #   - A collect page always has Bold-17pt name spans.
    #   - A bio page does NOT have Bold-17pt.
    #   - Bio pages typically precede their collect page.
    #   - Some entries (e.g. bracketed trial-use) may have no preceding bio page.
    #
    # Strategy: walk the page list; maintain a "pending bio" buffer.
    # When we hit a collect page, flush: create builder from pending bio + collect.
    # When we hit a bio page, set it as pending (previous pending was orphaned, unlikely).

    results: list[ParsedCommemoration] = []
    # For multi-page bios, collect all bio pages before the next collect page
    pending_bio_pages: list[_PageInfo] = []

    for pi in pages:
        if pi.has_new_entry:
            # This is a collect page — build the entry
            builder = _CommemorationBuilder()

            # Attach bio pages
            for bio_pi in pending_bio_pages:
                if not builder.date:
                    builder.date = bio_pi.date
                _parse_bio_page(bio_pi.spans, bio_pi.page_num, builder)

            pending_bio_pages = []

            if not builder.date:
                builder.date = pi.date

            _parse_collect_page(pi.spans, pi.page_num, builder)

            built = builder.build()
            if built is not None:
                results.append(built)
        else:
            # Bio page — buffer for next entry
            pending_bio_pages.append(pi)

    return results


# ---------------------------------------------------------------------------
# SHA256 verification
# ---------------------------------------------------------------------------

EXPECTED_SHA256 = "5deea4a131b6c90218ae07b92a17f68bfce000a24a04edd989cdb1edc332bfd7"


def verify_pdf_sha256(pdf_path: str | Path) -> bool:
    """Return True if the PDF at pdf_path matches the pinned SHA256."""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest() == EXPECTED_SHA256


# ---------------------------------------------------------------------------
# Convenience: parse from default fixture location
# ---------------------------------------------------------------------------

_DEFAULT_PDF = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "lff_2024.pdf"


def parse_lff_2024_default() -> list[ParsedCommemoration]:
    """Parse from the pinned fixture PDF at tests/fixtures/lff_2024.pdf."""
    return parse_lff_2024(_DEFAULT_PDF)
