"""BCP 1979 Collects parser.

Pure function: HTML files in → list[ParsedCollect] out.
No I/O beyond file reads, no DB, no network, no global state.

Structural notes from real fixture HTML (bcponline.org):
- A collect begins with a <p> containing a <strong> tag (the feast name).
  The <p> may carry an id= attribute (the feast slug); if not, we slugify
  the feast name ourselves.
- The body paragraph(s) follow immediately.  The body ends when we see
  <em>Amen.</em> (or <em>Amen</em>).  A page-break (rightfoot/leftfoot +
  <hr>) in the middle of a body is invisible to the reader; we skip the
  marker paragraphs and keep collecting body text.
- A <p><em>Preface of X</em></p> after the Amen is the preface.
- <p class="rubric"> paragraphs are rubric instructions.  They may appear
  before the feast heading (section-level rubric), between feast heading
  and body, between body and preface, or after preface (as a "or this"
  transition).
- <p class="rightfoot"> / <p class="leftfoot"> hold the printed-page number;
  we capture it into raw_metadata as page_number (int).
- <p class="topmenu"> is the in-page navigation ToC — skip entirely.
- Filename encodes rite (*t.html → rite_i / traditional,
  *c.html → rite_ii / contemporary) and section
  (seasons | holydays | common | various).

Section mapping stored in raw_metadata['section'].
canonical_id is constructed as "{section}_{feast_slug}_{rite}".
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

Rite = Literal["rite_i", "rite_ii"]

# ToC filenames that should short-circuit to empty output.
_TOC_FILENAMES: frozenset[str] = frozenset(
    {"collects.html", "toctradit.html", "toccontemp.html", "proper.html"}
)


@dataclass(frozen=True)
class ParsedCollect:
    """A single BCP 1979 collect, parsed from a cached HTML file."""

    feast_slug: str
    """Canonical `{name_snake}_anglican` slug derived from the feast name.

    Matches ``scripts/feast_import.py::_make_slug`` so BCP collects align
    with the feasts.yaml seed slug scheme.  The raw HTML ``id=`` attribute is
    preserved separately on ``source_anchor`` for traceability."""

    feast_name: str
    """Human-readable name from the <strong> tag (whitespace-normalised)."""

    rite: Rite
    """rite_i (Traditional) or rite_ii (Contemporary), from filename."""

    section: str
    """seasons | holydays | common | various (derived from filename stem)."""

    body_text: str
    """Collect body with <br/> flattened to single spaces, whitespace
    normalised, <em> content preserved as plain text."""

    rubrics: list[str]
    """Rubric paragraphs (class="rubric") associated with this collect,
    in document order."""

    preface: str | None
    """Preface line text (e.g. 'Preface of Advent'), if present."""

    source_file: str
    """Basename of the HTML file this collect was parsed from."""

    source_anchor: str | None
    """The id= attribute of the heading <p>, if present; else None."""

    page_number: int | None
    """Printed-page number from the nearest preceding rightfoot/leftfoot
    marker, for traceability."""

    canonical_id: str
    """Slug grouping Rite I/II counterparts:
    '{section}_{feast_slug}'.  Rite variant is in language_register."""

    raw_metadata: str
    """JSON string carrying: section, rite, page_number, source_anchor,
    source_file.  Downstream handler uses this to populate
    liturgical_unit_meta.raw_metadata."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_UNDERSCORE_RE = re.compile(r"[^a-z0-9]+")

_TRADITION_SUFFIX = "anglican"


def _slugify(text: str) -> str:
    """Convert feast-name text to the canonical `{name_snake}_anglican` slug.

    Matches ``scripts/feast_import.py::_make_slug``: lower-case, replace any
    run of non-``[a-z0-9]`` characters with a single underscore, strip leading
    and trailing underscores, then append the tradition suffix.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    name_part = _NON_ALNUM_UNDERSCORE_RE.sub("_", text).strip("_")
    return f"{name_part}_{_TRADITION_SUFFIX}"


def _extract_page_number(p: Tag) -> int | None:
    """Extract the integer page number from a rightfoot/leftfoot <p>."""
    text = p.get_text(separator=" ")
    # Page numbers appear as a bare integer somewhere in the text.
    # Pattern: either "...  159" (rightfoot) or "160  ..." (leftfoot)
    match = re.search(r"\b(\d{2,4})\b", text)
    if match:
        return int(match.group(1))
    return None


def _paragraph_text(p: Tag) -> str:
    """Flatten a <p> to plain text: <br/> → space, collapse whitespace."""
    parts: list[str] = []
    for child in p.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append(" ")
            else:
                # em, strong, small, a, span — treat as plain text
                parts.append(child.get_text())
    raw = "".join(parts)
    return _WS_RE.sub(" ", raw).strip()


def _css_classes(p: Tag) -> list[str]:
    """Return the CSS class list for an element as a list of strings."""
    raw = p.get("class")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    # AttributeValueList is list-like
    return list(raw)


def _is_page_marker(p: Tag) -> bool:
    css = _css_classes(p)
    return bool({"rightfoot", "leftfoot"} & set(css))


def _is_rubric(p: Tag) -> bool:
    return "rubric" in _css_classes(p)


def _is_topmenu(p: Tag) -> bool:
    return "topmenu" in _css_classes(p)


def _is_small_class(p: Tag) -> bool:
    """True for <p class="small"> or <p class="x-small"> paragraphs."""
    css = _css_classes(p)
    return bool({"small", "x-small"} & set(css))


def _has_strong(p: Tag) -> bool:
    """True if this <p> contains a <strong> child anywhere in it."""
    return bool(p.find("strong"))


def _strong_text(p: Tag) -> str:
    """Return whitespace-normalised text of the first <strong> in p."""
    strong = p.find("strong")
    if strong is None:
        return ""
    return _WS_RE.sub(" ", strong.get_text()).strip()


def _is_preface_paragraph(p: Tag) -> bool:
    """True if this paragraph is solely a preface marker.

    Pattern: <p><em>Preface of X</em></p>  (possibly with surrounding
    whitespace).  We check that the stripped text of all non-whitespace
    content starts with 'Preface'.
    """
    text = _paragraph_text(p)
    return text.startswith("Preface ")


def _amen_in_paragraph(p: Tag) -> bool:
    """True if 'Amen' appears anywhere in this paragraph."""
    return "Amen" in p.get_text()


def _infer_rite_and_section(source_file: str) -> tuple[Rite, str]:
    """Derive rite and section from the filename.

    filename stem:
      seasonst → section=seasons, rite=rite_i
      seasonsc → section=seasons, rite=rite_ii
      holydayst → section=holydays, rite=rite_i
      holydaysc → section=holydays, rite=rite_ii
      commont → section=common, rite=rite_i
      commonc → section=common, rite=rite_ii
      varioust → section=various, rite=rite_i
      variousc → section=various, rite=rite_ii
    """
    stem = Path(source_file).stem  # e.g. "seasonst"
    if stem.endswith("t"):
        rite: Rite = "rite_i"
        section_raw = stem[:-1]  # strip trailing 't'
    elif stem.endswith("c"):
        rite = "rite_ii"
        section_raw = stem[:-1]  # strip trailing 'c'
    else:
        # Unknown — default gracefully
        logger.warning("Cannot determine rite from filename %s; defaulting to rite_i", source_file)
        rite = "rite_i"
        section_raw = stem

    # Normalise known section names
    section_map = {
        "seasons": "seasons",
        "holydays": "holydays",
        "common": "common",
        "various": "various",
    }
    section = section_map.get(section_raw, section_raw)
    return rite, section


def _is_toc_file(source_file: str, html: str) -> bool:
    """Return True if this file should be skipped as a ToC / index page.

    Detection rules (any one sufficient):
    1. Filename is in the known ToC set.
    2. HTML body < 1024 bytes with no <strong> tag.
    """
    basename = Path(source_file).name
    if basename in _TOC_FILENAMES:
        return True
    return bool(len(html) < 1024 and "<strong>" not in html)


# ---------------------------------------------------------------------------
# State machine for parsing
# ---------------------------------------------------------------------------

# Parser states
_IDLE = "idle"
_IN_HEADING = "in_heading"  # saw <strong>, waiting for body
_IN_BODY = "in_body"        # collecting body paragraphs
_AFTER_AMEN = "after_amen"  # body complete, may see preface / rubric


@dataclass
class _CollectBuilder:
    """Mutable accumulator for a single collect being assembled."""

    feast_slug: str = ""
    feast_name: str = ""
    source_anchor: str | None = None
    body_parts: list[str] | None = None
    rubrics: list[str] | None = None
    preface: str | None = None
    page_number: int | None = None

    def add_body_part(self, text: str) -> None:
        if self.body_parts is None:
            self.body_parts = []
        self.body_parts.append(text)

    def add_rubric(self, text: str) -> None:
        if self.rubrics is None:
            self.rubrics = []
        self.rubrics.append(text)

    @property
    def body_text(self) -> str:
        if not self.body_parts:
            return ""
        joined = " ".join(self.body_parts)
        return _WS_RE.sub(" ", joined).strip()


# ---------------------------------------------------------------------------
# Public parse functions
# ---------------------------------------------------------------------------


def parse_collects_file(html: str, source_file: str) -> list[ParsedCollect]:
    """Parse one cached BCP 1979 collects HTML file.

    Args:
        html:        Full HTML string of the file.
        source_file: Basename of the file (used for rite/section inference
                     and stored in output for traceability).

    Returns:
        A list of ParsedCollect dataclasses, one per collect.
        Returns [] for ToC / index files.
    """
    if _is_toc_file(source_file, html):
        return []

    rite, section = _infer_rite_and_section(source_file)
    soup = BeautifulSoup(html, "lxml")

    results: list[ParsedCollect] = []
    current: _CollectBuilder | None = None
    state: str = _IDLE
    current_page: int | None = None

    # We iterate over top-level block elements inside <body>.
    # BeautifulSoup lxml parser wraps everything in <html><body> automatically.
    body = soup.find("body")
    if body is None:
        return []

    # Collect all direct-ish paragraph-level tags.  We process siblings of
    # the body, walking all top-level elements in order.
    elements = [el for el in body.children if isinstance(el, Tag)]

    def _finalise(builder: _CollectBuilder) -> None:
        """Emit a ParsedCollect from a completed builder."""
        if not builder.feast_name or not builder.body_text:
            return
        slug = builder.feast_slug or _slugify(builder.feast_name)
        canonical_id = f"{section}_{slug}"
        raw_meta = json.dumps(
            {
                "section": section,
                "rite": rite,
                "page_number": builder.page_number,
                "source_anchor": builder.source_anchor,
                "source_file": source_file,
            },
            ensure_ascii=False,
        )
        results.append(
            ParsedCollect(
                feast_slug=slug,
                feast_name=builder.feast_name,
                rite=rite,
                section=section,
                body_text=builder.body_text,
                rubrics=builder.rubrics or [],
                preface=builder.preface,
                source_file=source_file,
                source_anchor=builder.source_anchor,
                page_number=builder.page_number,
                canonical_id=canonical_id,
                raw_metadata=raw_meta,
            )
        )

    for el in elements:
        tag_name = el.name

        # ----------------------------------------------------------------
        # Page markers — update current_page, never affect state machine
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_page_marker(el):
            page_num = _extract_page_number(el)
            if page_num is not None:
                current_page = page_num
                if current is not None and current.page_number is None:
                    current.page_number = current_page
            continue

        # ----------------------------------------------------------------
        # Skip navigation / small-print paragraphs
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_topmenu(el):
            continue

        if tag_name in ("hr", "br"):
            continue

        if tag_name in ("h1", "h2", "h3", "h4"):
            # Section headings (e.g. <h2>Holy Days</h2>) — skip
            continue

        # ----------------------------------------------------------------
        # Rubric paragraphs
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_rubric(el):
            rubric_text = _paragraph_text(el)
            if not rubric_text:
                continue
            if state in (_IDLE,):
                # Section-level rubric before any feast heading — ignore
                # (we don't accumulate orphan rubrics)
                pass
            elif state == _IN_HEADING:
                # Rubric between heading and body
                if current is not None:
                    current.add_rubric(rubric_text)
            elif state == _IN_BODY:
                # Rubric mid-body is unusual but possible (e.g. "or this")
                # We treat it as associated with the CURRENT collect.
                if current is not None:
                    current.add_rubric(rubric_text)
            elif state == _AFTER_AMEN and current is not None:
                # Rubric after amen — could be "or this" transition, which
                # signals another variant body follows.  Associate with
                # current collect, then return to IN_HEADING to catch
                # the next body.
                current.add_rubric(rubric_text)
                # Stay in AFTER_AMEN; if a body follows without a new
                # heading it will be caught below.
            continue

        # ----------------------------------------------------------------
        # <p class="small"> / <p class="x-small"> — skip (citations,
        # introductory notes)
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_small_class(el):
            continue

        # ----------------------------------------------------------------
        # Paragraphs with <strong> — new collect heading
        # ----------------------------------------------------------------
        if tag_name == "p" and _has_strong(el):
            # Finalise any in-progress collect first
            if current is not None:
                _finalise(current)

            raw_name = _strong_text(el)
            # Strip leading "N. " numeric prefix (e.g. "1. Of the Holy Trinity"
            # → "Of the Holy Trinity").  This is presentational in the Various
            # Occasions files and should not leak into feast_name or the slug.
            feast_name = re.sub(r"^\d+\.\s+", "", raw_name)

            p_id: str | None = el.get("id")  # type: ignore[assignment]
            if p_id is not None and re.fullmatch(r"\d+", p_id):
                # Purely numeric id (e.g. "1", "25") — use as source_anchor
                # for traceability but fall back to slugifying the cleaned name
                # for feast_slug, since numeric anchors are meaningless as
                # semantic feast identifiers.
                slug = _slugify(feast_name)
                source_anchor: str | None = p_id
            elif p_id is not None:
                # Non-numeric id (e.g. "advent", "IndependenceDay") — keep as
                # source_anchor for traceability but still derive feast_slug
                # from the feast name so every collect follows the canonical
                # `{name_snake}_anglican` scheme.
                slug = _slugify(feast_name)
                source_anchor = p_id
            else:
                slug = _slugify(feast_name)
                source_anchor = None

            current = _CollectBuilder(
                feast_slug=slug,
                feast_name=feast_name,
                source_anchor=source_anchor,
                page_number=current_page,
            )
            state = _IN_HEADING
            continue

        # ----------------------------------------------------------------
        # Plain <p> without <strong> — body, preface, or continuation
        # ----------------------------------------------------------------
        if tag_name == "p":
            text = _paragraph_text(el)
            if not text:
                continue

            if state == _IDLE:
                # Body text before any heading — unlikely but skip
                continue

            if state == _IN_HEADING:
                # This is the first body paragraph for the current collect
                if current is not None:
                    current.add_body_part(text)
                    state = _AFTER_AMEN if _amen_in_paragraph(el) else _IN_BODY
                continue

            if state == _IN_BODY:
                # Could be body continuation or — after Amen — preface
                if _amen_in_paragraph(el):
                    if current is not None:
                        current.add_body_part(text)
                    state = _AFTER_AMEN
                elif _is_preface_paragraph(el):
                    # Preface line found (shouldn't be here yet, but handle)
                    if current is not None:
                        current.preface = text
                    state = _AFTER_AMEN
                else:
                    # Body continuation (page-break split)
                    if current is not None:
                        current.add_body_part(text)
                continue

            if state == _AFTER_AMEN:
                if _is_preface_paragraph(el):
                    if current is not None and current.preface is None:
                        current.preface = text
                    # After capturing preface, stay in AFTER_AMEN.
                    # The next heading will trigger finalise.
                elif _amen_in_paragraph(el):
                    # "or this" alternate body following a rubric
                    # Append to current collect's body
                    if current is not None:
                        current.add_body_part(text)
                    # Stay in AFTER_AMEN
                else:
                    # Non-Amen, non-preface paragraph after Amen —
                    # could be an "or this" body continuation or
                    # a section note.  If it looks like a collect body
                    # fragment (prose paragraph), append it; otherwise skip.
                    # Heuristic: if paragraph has no id and no strong, and
                    # current collect exists, it's likely an alternate body.
                    if current is not None:
                        current.add_body_part(text)
                        # Re-check for Amen in this continuation
                        if _amen_in_paragraph(el):
                            state = _AFTER_AMEN
                continue

    # Finalise last collect
    if current is not None:
        _finalise(current)

    return results


def parse_collects_dir(path: Path) -> list[ParsedCollect]:
    """Parse all non-ToC BCP 1979 collects HTML files in a directory.

    Args:
        path: Directory containing cached .html files.

    Returns:
        Concatenated list of ParsedCollect from all content files.
    """
    results: list[ParsedCollect] = []
    for html_file in sorted(path.glob("*.html")):
        source_file = html_file.name
        html = html_file.read_text(encoding="utf-8", errors="replace")
        collects = parse_collects_file(html, source_file)
        results.extend(collects)
    return results
