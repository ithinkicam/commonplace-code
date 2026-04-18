"""BCP 1979 Prayers and Thanksgivings parser.

Pure function: HTML files in → list[ParsedPrayer] out.
No I/O beyond file reads, no DB, no network, no global state.

Sources:
  ~/commonplace/cache/bcp_1979/www.bcponline.org/Misc/Prayers.html
  ~/commonplace/cache/bcp_1979/www.bcponline.org/Misc/Thanksgivings.html

Structural notes from real HTML (bcponline.org/Misc/Prayers.html):
- Prayers 1–70 are anchor-addressable via ``<a name="N">`` elements embedded
  within ``<em>`` tags at the top level of ``<body>`` — most body text is NOT
  inside ``<p>`` tags but appears as flat NavigableString / ``<em>`` / ``<br>``
  siblings of ``<body>``.
- Section headings: ``<strong><a name="...">Title</a></strong>`` where the
  anchor name is non-numeric (e.g. ``"Prayers for the Church"``).  Some
  section anchors are bare ``<a name="...">`` inside ``<strong>``.
- Page-break markers: ``<p class="leftfoot|rightfoot">`` followed by ``<hr>``.
- Rubric cross-references: ``<em class="rubric">...</em>`` inline, or
  ``<p class="rubric">...</p>`` at block level.  We skip these from the
  prayer body.
- The body text of each prayer lives between its ``<a name="N">`` anchor and
  the next prayer anchor (or section anchor).  ``<em>Amen.</em>`` terminates
  the "canonical" body, but alternate forms (for prayer 70 "Grace at Meals")
  may follow on the same prayer.
- Thanksgivings.html follows the same structural pattern.

Genre / category decisions:
- Prayers 1–70 → genre="prayer"
- Thanksgivings 1–11 → genre="thanksgiving"
- category="devotional_manual" for both (supplementary prayers for private or
  occasional use, not appointed propers per the schema comment in the plan).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_TRADITION_SUFFIX = "anglican"

# "1. Title" or "12. Long\n    Title" at start of anchor text
_PRAYER_HEADING_RE = re.compile(r"^(\d+)\.\s+(.*)", re.DOTALL)

# Rubric cross-reference prefixes — we skip these from prayer bodies.
_CROSS_REF_PREFIXES = (
    "see also",
    "see the",
    "for use",
    "for optional",
    "prayers for the sick",
    "prayers for the dying",
    "prayers for the dead",
    "for education",
    "for social service",
    "for industry",
    "the general thanksgiving",
    "prayers for friday",
    "a prayer for parents",
    "for those to be ordained",
    "prayers originally composed",
)

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedPrayer:
    """A single BCP 1979 Prayer or Thanksgiving."""

    slug: str
    """Canonical ``{name_snake}_{type}_{N}_anglican`` slug.

    type is ``prayer`` for Prayers 1–70 and ``thanksgiving`` for
    Thanksgivings 1–11.  The numeric suffix guarantees uniqueness even when
    two prayers share the same title (e.g. prayers 50 and 51 are both
    "For a Birthday").  Follows the same name_snake helper as
    ``scripts/feast_import.py::_make_slug``."""

    title: str
    """Human-readable title (whitespace-normalised, numeric prefix stripped)."""

    prayer_number: int
    """Sequential number within the file: 1–70 for prayers; 1–11 for
    thanksgivings (raw number from the BCP HTML anchor)."""

    section_header: str
    """Section heading under which this prayer falls (e.g.
    ``"Prayers for National Life"``)."""

    body_text: str
    """Full prayer body; ``<br/>`` → space; whitespace normalised; no HTML
    tags; rubric cross-references excluded."""

    rubrics: list[str]
    """Rubric paragraphs (``<p class="rubric">`` or ``<em class="rubric">``)
    encountered within this prayer's span, in document order."""

    genre: str
    """``"prayer"`` (Prayers 1–70) or ``"thanksgiving"`` (Thanksgivings
    1–11)."""

    source_file: str
    """Basename of the HTML file this prayer was parsed from."""

    source_anchor: str | None
    """Value of the ``name=`` attribute on the heading anchor (e.g. ``"3"``)."""

    page_number: int | None
    """Printed-page number from the nearest preceding leftfoot/rightfoot
    marker, for traceability."""

    canonical_id: str
    """Equals ``slug``; provided for cross-referencing symmetry with other
    liturgical parsers."""

    raw_metadata: str
    """JSON string containing: prayer_number, section_header, source_anchor,
    source_file, page_number, genre, category, tradition, source.
    Thanksgivings also carry thanksgiving_number mirroring prayer_number."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_name_snake(text: str) -> str:
    """Convert a title to a snake_case name part (no suffix)."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    return _NON_ALNUM_RE.sub("_", text).strip("_")


def _slugify(text: str, genre: str, number: int) -> str:
    """Build the canonical slug: ``{name_snake}_{type}_{N}_anglican``.

    - Prayers (genre="prayer"):         ``{name_snake}_prayer_{N}_anglican``
    - Thanksgivings (genre="thanksgiving"): ``{name_snake}_thanksgiving_{N}_anglican``

    The type+number suffix guarantees uniqueness even when two prayers share
    the same title (e.g. prayers 50 and 51 are both "For a Birthday").
    """
    name_part = _make_name_snake(text)
    type_label = "prayer" if genre == "prayer" else "thanksgiving"
    return f"{name_part}_{type_label}_{number}_{_TRADITION_SUFFIX}"


def _fallback_slug(genre: str, number: int) -> str:
    """Stable fallback for a prayer without a parseable title."""
    type_label = "prayer" if genre == "prayer" else "thanksgiving"
    return f"{type_label}_{number}_{_TRADITION_SUFFIX}"


def _looks_like_cross_ref(text: str) -> bool:
    """Return True if ``text`` looks like a rubric cross-reference to skip."""
    lower = text.lower().lstrip()
    return any(lower.startswith(prefix) for prefix in _CROSS_REF_PREFIXES)


def _css_classes(tag: Tag) -> list[str]:
    raw = tag.get("class")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _is_page_marker(tag: Tag) -> bool:
    return bool({"rightfoot", "leftfoot"} & set(_css_classes(tag)))


def _extract_page_number(tag: Tag) -> int | None:
    text = tag.get_text(separator=" ")
    m = re.search(r"\b(\d{3,4})\b", text)
    return int(m.group(1)) if m else None


def _is_rubric(tag: Tag) -> bool:
    return "rubric" in _css_classes(tag)


def _flatten_text(tag: Tag) -> str:
    """Recursively flatten a tag to plain text; ``<br/>`` → space."""
    parts: list[str] = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append(" ")
            else:
                parts.append(_flatten_text(child))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Token-stream parser
# ---------------------------------------------------------------------------


@dataclass
class _Builder:
    """Accumulates text for one prayer being assembled."""

    number: int
    title: str
    anchor: str
    section: str
    genre: str
    page: int | None
    tokens: list[str] = field(default_factory=list)
    rubrics: list[str] = field(default_factory=list)

    @property
    def body_text(self) -> str:
        raw = " ".join(self.tokens)
        return _WS_RE.sub(" ", raw).strip()


def parse_prayers_file(
    html: str,
    source_file: str,
    *,
    genre: str = "prayer",
) -> list[ParsedPrayer]:
    """Parse one BCP 1979 Prayers or Thanksgivings HTML file.

    Strategy
    --------
    We walk ``body.descendants`` in document order, emitting a flat token
    stream.  When we encounter an ``<a name="N">`` anchor with a numeric
    name matching the pattern ``"N. Title"``, we start a new prayer
    builder.  When we encounter the next prayer anchor (or end of document)
    we finalise and emit the completed prayer.

    Text is collected from:
    - NavigableString nodes that are direct children of ``<body>`` or of
      inline tags (``<em>``, ``<strong>``, ``<a>``).
    - ``<p>`` tags that are NOT page markers and NOT rubric paragraphs
      (those are captured separately).

    Args:
        html:        Full HTML string.
        source_file: Basename for traceability.
        genre:       ``"prayer"`` or ``"thanksgiving"``.

    Returns:
        List of ParsedPrayer in document order.
    """
    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    if body is None:
        return []

    # First pass: collect all named anchors in document order to build an
    # anchor → position index.  We'll use the <a name> elements themselves
    # as section markers in the second pass.

    results: list[ParsedPrayer] = []
    current: _Builder | None = None
    current_section: str = ""
    current_page: int | None = None

    # -----------------------------------------------------------------------
    # We need to walk body children at the *top level* only (not recursing
    # into inline tags ourselves) so that we collect body text in the right
    # order.  BUT each top-level child might be an <em> containing an anchor
    # that starts a new prayer, followed by more inline siblings.
    #
    # Approach: iterate body.children; for each child classify it.
    # -----------------------------------------------------------------------

    def _flush(builder: _Builder) -> None:
        text = builder.body_text
        if not builder.title or not text:
            return
        # Strip malformed "(1979 Version)" prefix from Thanksgiving 1 body.
        if genre == "thanksgiving" and builder.number == 1:
            text = re.sub(r"^\s*\(1979 Version\)\s*", "", text)
        slug = _slugify(builder.title, genre, builder.number)
        raw: dict[str, object] = {
            "prayer_number": builder.number,
            "section_header": builder.section,
            "source_anchor": builder.anchor,
            "source_file": source_file,
            "page_number": builder.page,
            "genre": builder.genre,
            "category": "devotional_manual",
            "tradition": "anglican",
            "source": "bcp_1979",
        }
        if genre == "thanksgiving":
            raw["thanksgiving_number"] = builder.number
        results.append(
            ParsedPrayer(
                slug=slug,
                title=builder.title,
                prayer_number=builder.number,
                section_header=builder.section,
                body_text=text,
                rubrics=list(builder.rubrics),
                genre=builder.genre,
                source_file=source_file,
                source_anchor=builder.anchor,
                page_number=builder.page,
                canonical_id=slug,
                raw_metadata=json.dumps(raw, ensure_ascii=False),
            )
        )

    def _process_node(node: Tag | NavigableString, collect_text: bool) -> None:
        """Process a single node, dispatching based on type and context."""
        nonlocal current, current_section, current_page

        if isinstance(node, NavigableString):
            text = str(node)
            stripped = text.strip()
            if stripped and current is not None and collect_text:
                current.tokens.append(stripped)
            return

        if not isinstance(node, Tag):
            return

        tag = node

        # Page markers — update current_page; do not collect text.
        if tag.name == "p" and _is_page_marker(tag):
            pn = _extract_page_number(tag)
            if pn is not None:
                current_page = pn
                if current is not None and current.page is None:
                    current.page = current_page
            return

        # hr / br at block level — skip
        if tag.name in ("hr",):
            return

        # h2 — skip
        if tag.name in ("h1", "h2", "h3", "h4"):
            return

        # <p class="rubric"> — capture as rubric
        if tag.name == "p" and _is_rubric(tag):
            rubric_text = _WS_RE.sub(" ", _flatten_text(tag)).strip()
            if rubric_text and current is not None:
                current.rubrics.append(rubric_text)
            return

        # <strong> containing a non-numeric named anchor → section header
        if tag.name == "strong":
            a_tags = tag.find_all("a", attrs={"name": True})
            section_found = False
            for a in a_tags:
                name_val = str(a.get("name", ""))
                if name_val and not re.fullmatch(r"\d+", name_val):
                    section_found = True
                    # Anchor text may be empty (e.g. "Prayers for National Life"
                    # has an empty <a name="..."></a> with text after it in
                    # the strong tag).  Fall back to strong's full text.
                    raw_section = _WS_RE.sub(" ", _flatten_text(a)).strip()
                    if not raw_section:
                        raw_section = _WS_RE.sub(" ", _flatten_text(tag)).strip()
                    if raw_section:
                        current_section = raw_section
            if section_found:
                # Also process children for numeric prayer anchors inside
                _process_children(tag, collect_text)
                return
            # Strong might also contain numeric prayer anchors; fall through
            # to handle children.
            _process_children(tag, collect_text)
            return

        # Helper: try to start a new prayer from a named <a> tag.
        # Returns True if a prayer heading was found and started.
        def _try_start_prayer_from_anchor(a_tag: Tag) -> bool:
            nonlocal current  # noqa: F841 — reassigned below
            name_val = str(a_tag.get("name", ""))
            if not re.fullmatch(r"\d+", name_val):
                return False
            anchor_text = _WS_RE.sub(" ", _flatten_text(a_tag)).strip()
            m = _PRAYER_HEADING_RE.match(anchor_text)
            if not m:
                return False
            if current is not None:
                _flush(current)
            num = int(m.group(1))
            title = _WS_RE.sub(" ", m.group(2).replace("\n", " ")).strip()
            current = _Builder(
                number=num,
                title=title,
                anchor=name_val,
                section=current_section,
                genre=genre,
                page=current_page,
            )
            return True

        # <em> tag — check for prayer heading anchor inside it
        if tag.name == "em":
            # Check for rubric em
            if _is_rubric(tag):
                rubric_text = _WS_RE.sub(" ", _flatten_text(tag)).strip()
                if rubric_text and current is not None and not _looks_like_cross_ref(rubric_text):
                    current.rubrics.append(rubric_text)
                return

            # Check if this em contains a prayer heading anchor
            a_tag = tag.find("a", attrs={"name": True})
            if a_tag is not None:
                name_val = str(a_tag.get("name", ""))
                if re.fullmatch(r"\d+", name_val):
                    if _try_start_prayer_from_anchor(a_tag):
                        return
                    # Numeric anchor but no heading match — fall through to text
                elif name_val and not re.fullmatch(r"\d+", name_val):
                    # Non-numeric anchor inside <em> — section header
                    raw_section = _WS_RE.sub(" ", _flatten_text(a_tag)).strip()
                    if raw_section:
                        current_section = raw_section
                    return

            # Plain <em> — collect its text as body
            if current is not None and collect_text:
                em_text = _WS_RE.sub(" ", _flatten_text(tag)).strip()
                if em_text and not _looks_like_cross_ref(em_text):
                    current.tokens.append(em_text)
            return

        # <br> inline — separator; we just continue (spaces inserted elsewhere)
        if tag.name == "br":
            return

        # <p> tag (no special class) — collect its text
        if tag.name == "p":
            # Check if p contains prayer heading anchors
            a_tags_in_p = tag.find_all("a", attrs={"name": True})
            numeric_anchors = [
                a for a in a_tags_in_p
                if re.fullmatch(r"\d+", str(a.get("name", "")))
            ]

            if numeric_anchors:
                # This paragraph contains prayer headings and/or section
                # markers interleaved.  Process children in document order
                # so that section headers are set at the right point in the
                # stream relative to the prayer anchors.
                _process_children(tag, collect_text)
                return

            # No numeric prayer anchors — check for section anchors only
            non_numeric_anchors = [
                a for a in a_tags_in_p
                if a.get("name") and not re.fullmatch(r"\d+", str(a.get("name", "")))
            ]
            for a in non_numeric_anchors:
                raw_section = _WS_RE.sub(" ", _flatten_text(a)).strip()
                if raw_section:
                    current_section = raw_section

            # Plain paragraph — collect text
            p_text = _WS_RE.sub(" ", _flatten_text(tag)).strip()
            if p_text and current is not None and collect_text and not _looks_like_cross_ref(p_text):
                current.tokens.append(p_text)
            return

        # <a> tag — either a section anchor or a bare prayer heading anchor
        # (Thanksgivings 3/4 use <p><a name="3">3. Title</a></p> pattern).
        if tag.name == "a":
            name_val = str(tag.get("name", ""))
            if not name_val:
                return
            if re.fullmatch(r"\d+", name_val):
                _try_start_prayer_from_anchor(tag)
            else:
                raw_section = _WS_RE.sub(" ", _flatten_text(tag)).strip()
                if raw_section:
                    current_section = raw_section
            return

        # Anything else — recurse
        _process_children(tag, collect_text)

    def _process_children(parent: Tag, collect_text: bool) -> None:
        for child in parent.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text and current is not None and collect_text and not _looks_like_cross_ref(text):
                    current.tokens.append(text)
            elif isinstance(child, Tag):
                _process_node(child, collect_text)

    # Main loop over body's direct children
    for child in body.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text and current is not None and not _looks_like_cross_ref(text):
                current.tokens.append(text)
        elif isinstance(child, Tag):
            _process_node(child, collect_text=True)

    # Flush last prayer
    if current is not None:
        _flush(current)

    return results


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_prayers_and_thanksgivings(
    prayers_html_path: Path,
    thanksgivings_html_path: Path,
) -> list[ParsedPrayer]:
    """Parse both Prayers.html and Thanksgivings.html.

    Args:
        prayers_html_path:       Path to the cached Prayers.html file.
        thanksgivings_html_path: Path to the cached Thanksgivings.html file.

    Returns:
        Combined list of ParsedPrayer: 70 prayers (genre=``"prayer"``) +
        11 thanksgivings (genre=``"thanksgiving"``), in document order.
    """
    prayers_html = prayers_html_path.read_text(encoding="utf-8", errors="replace")
    thanks_html = thanksgivings_html_path.read_text(encoding="utf-8", errors="replace")

    prayers = parse_prayers_file(
        prayers_html,
        source_file=prayers_html_path.name,
        genre="prayer",
    )
    thanksgivings = parse_prayers_file(
        thanks_html,
        source_file=thanksgivings_html_path.name,
        genre="thanksgiving",
    )
    return prayers + thanksgivings
