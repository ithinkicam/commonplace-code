"""BCP 1979 Daily Office parser.

Pure function: HTML files in → list[ParsedOffice] out.
No I/O beyond file reads, no DB, no network, no global state.

Structural notes from real fixture HTML (bcponline.org):
- Units begin with a <p> containing a <strong> child.
  - When <strong> contains only a number (e.g. "9"), the full <p> text
    carries the complete heading: "9  The First Song of Isaiah  Ecce, Deus  Isaiah 12:2-6".
    We use the full <p> text to derive the name.
  - When <strong> contains the full name, _strong_text is used.
- Collect-style headings ("A Collect for X", "The General Thanksgiving",
  etc.) are plain <p> paragraphs (no <strong>).  The very next <p>
  is the body.  We detect these via a regex.
- <p id="..."> anchors without <strong> (e.g. id="morning", id="confession",
  id="sentences") are also treated as unit starts; their text is the heading.
- Seasonal-sentence blocks in mp1/mp2/ep1/ep2 are plain <p> paragraphs whose
  text begins with a liturgical season name followed by the scripture sentence.
  We emit one unit per seasonal sentence.
- Versicle/response pairs live in <table class="vrtable"> or similar.
  We flatten them and append to the current unit's body.
- <p class="rubric"> → rubric paragraphs associated with the current unit.
- <p class="rightfoot"> / <p class="leftfoot"> → page-number markers.
- Skip: <p class="topmenu">, <p class="smaller">, <p class="small">,
  <p class="x-small">, <hr>, <br>, <h1>, <h2>, <h3>.
- Files that are ToC/index pages (dailyoff.html, concernmp1.html, etc.) → [].

Office/rite derivation from filename:
  mp1.html        → morning_prayer,  rite_i
  mp2.html        → morning_prayer,  rite_ii
  ep1.html        → evening_prayer,  rite_i
  ep2.html        → evening_prayer,  rite_ii
  compline.html   → compline,        none
  noonday.html    → noonday,         none
  devotion.html   → daily_devotions, none
  devotion2.html  → daily_devotions, none
  canticle.html   → canticle,        both
  evening.html    → evening_prayer,  none
  Litany.html     → great_litany,    none
  concernevening / concernmp1 / concernmp2 / direct / dailyoff → [] (ToC)

kind taxonomy:
  canticle          — named scriptural song (Te Deum, Magnificat, Venite …)
  prayer            — collect or free-form prayer ending in Amen
  creed             — Apostles' Creed, Nicene Creed
  psalm_ref         — placeholder heading ("The Psalm or Psalms Appointed")
  seasonal_sentence — opening scripture sentences keyed to a season
  versicle_response — short versicle/response exchange (suffrages, Lord's Prayer)
  rubric_block      — section that is predominantly rubrical / instructional
  intro             — section-level introduction or explanatory block
  suffrage          — litany/suffrages block (A / B sets in Evening Prayer,
                       Great Litany supplication groups)
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

OfficeLiteral = Literal[
    "morning_prayer",
    "evening_prayer",
    "compline",
    "noonday",
    "daily_devotions",
    "canticle",
    "great_litany",
]

RiteLiteral = Literal["rite_i", "rite_ii", "both", "none"]

KindLiteral = Literal[
    "canticle",
    "prayer",
    "creed",
    "psalm_ref",
    "seasonal_sentence",
    "versicle_response",
    "rubric_block",
    "intro",
    "suffrage",
]

# ---------------------------------------------------------------------------
# ToC / skip filenames
# ---------------------------------------------------------------------------

_TOC_FILENAMES: frozenset[str] = frozenset(
    {
        "dailyoff.html",
        "concernmp1.html",
        "concernmp2.html",
        "concernevening.html",
        "direct.html",
    }
)

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedOffice:
    """A single BCP 1979 Daily Office unit parsed from a cached HTML file."""

    slug: str
    """Canonical ``{name_snake}_anglican`` slug."""

    name: str
    """Human-readable heading text."""

    rite: RiteLiteral
    """rite_i / rite_ii / both / none — derived from filename."""

    office: OfficeLiteral
    """morning_prayer / evening_prayer / compline / noonday /
    daily_devotions / canticle / great_litany — derived from filename."""

    kind: KindLiteral
    """Semantic kind of unit."""

    body_text: str
    """Cleaned prose body with <br/> flattened, whitespace normalised."""

    rubrics: tuple[str, ...]
    """Rubric paragraphs (class="rubric") in document order."""

    source_file: str
    """Relative path from repo root."""

    source_anchor: str | None
    """Raw ``id=`` attribute of the heading element, if present."""

    page_number: int | None
    """Printed-page number from the nearest preceding rightfoot/leftfoot marker."""

    canonical_id: str
    """Same as slug (currently identical)."""

    raw_metadata: dict[str, Any]
    """Free-form dict for additional signals:
    - "season": str      (for seasonal_sentence units)
    - "office": str
    - "rite": str
    - "page_number": int | None
    - "source_anchor": str | None
    - "source_file": str
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_UNDERSCORE_RE = re.compile(r"[^a-z0-9]+")
_TRADITION_SUFFIX = "anglican"

# Regex matching a numeric prefix on canticle headings like "9  The First Song…"
_NUMERIC_PREFIX_RE = re.compile(r"^\s*(\d+)\s*[.\s]+")

# Regex for collect/prayer headings (plain paragraphs without <strong>).
# These are the *title* lines that introduce a collect/prayer body.
# Prayer body starters (like "Almighty God, our Father...") are NOT included;
# they are handled by the lookahead length check in _is_collect_heading.
_COLLECT_HEADING_RE = re.compile(
    r"^(A Collect\b|The Collect\b|The General Thanksgiving|A Prayer of\b|"
    r"Prayer of\b)",
    re.IGNORECASE,
)

# Season names for detecting opening-sentence seasonal labels
_SEASON_NAMES: tuple[str, ...] = (
    "Easter Season",
    "Holy Week",
    "Trinity Sunday",
    "All Saints",
    "At any Time",
    "Occasions of Thanksgiving",
    "Ascension Day",
    "Day of Pentecost",
    "Advent",
    "Christmas",
    "Epiphany",
    "Lent",
    "Easter",
    "Trinity",
)  # Ordered longest-first to avoid prefix collisions

# Canticle name keywords
_CANTICLE_KEYWORDS: frozenset[str] = frozenset(
    {
        "venite",
        "jubilate",
        "pascha nostrum",
        "christ our passover",
        "te deum",
        "you are god",
        "benedictus",
        "magnificat",
        "nunc dimittis",
        "gloria in excelsis",
        "glory to god",
        "glory be to god",
        "phos hilaron",
        "o gracious light",
        "benedicite",
        "song of creation",
        "song of praise",
        "song of penitence",
        "song of isaiah",
        "song of mary",
        "song of zechariah",
        "song of simeon",
        "song of moses",
        "song to the lamb",
        "song of the redeemed",
        "glory of god",
        "kyrie pantokrator",
        "benedictus es",
        "dignus es",
        "quaerite dominum",
        "surge illuminare",
        "ecce deus",
        "cantemus domino",
        "magna et mirabilia",
        "we praise thee",
        "psalm 95",
        "psalm 100",
        "psalm 67",
        "psalm 98",
    }
)


def _slugify(text: str) -> str:
    """Convert unit name to the canonical ``{name_snake}_anglican`` slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    name_part = _NON_ALNUM_UNDERSCORE_RE.sub("_", text).strip("_")
    return f"{name_part}_{_TRADITION_SUFFIX}"


def _extract_page_number(p: Tag) -> int | None:
    """Extract integer page number from a rightfoot/leftfoot <p>."""
    text = p.get_text(separator=" ")
    match = re.search(r"\b(\d{2,4})\b", text)
    if match:
        return int(match.group(1))
    return None


def _paragraph_text(el: Tag) -> str:
    """Flatten a tag to plain text; <br/> → space; collapse whitespace."""
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


def _css_classes(el: Tag) -> list[str]:
    raw = el.get("class")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _is_page_marker(el: Tag) -> bool:
    return bool({"rightfoot", "leftfoot"} & set(_css_classes(el)))


def _is_rubric(el: Tag) -> bool:
    return "rubric" in _css_classes(el)


def _is_skip_class(el: Tag) -> bool:
    css = set(_css_classes(el))
    return bool(css & {"topmenu", "small", "x-small", "smaller"})


def _has_strong(el: Tag) -> bool:
    return bool(el.find("strong"))


def _strong_text(el: Tag) -> str:
    strong = el.find("strong")
    if strong is None:
        return ""
    return _WS_RE.sub(" ", strong.get_text()).strip()


def _full_p_text(el: Tag) -> str:
    return _paragraph_text(el)


def _extract_heading_from_p(el: Tag) -> str:
    """Extract the unit heading from a <p> with a <strong> child.

    When <strong> contains only a number (BCP canticle numbering), the
    full heading text is embedded in the <p>'s text nodes (with NBSP separators
    between fields).  We use the raw ``get_text()`` (pre-WS-normalisation)
    so that the NBSP clusters survive and we can cleanly trim the scripture
    citation.
    """
    strong_text = _strong_text(el)
    # If strong contains only digits, use the full raw paragraph text
    if re.fullmatch(r"\d+", strong_text):
        # Use get_text() without separator to preserve original spacing
        raw_full = el.get_text()
        return _clean_numbered_heading(raw_full)
    # Otherwise clean up the strong text itself
    return _clean_strong_heading(strong_text)


def _clean_numbered_heading(raw: str) -> str:
    """Strip leading number + trailing scripture citation from a heading.

    Input:  ' 9  The First Song of Isaiah  Ecce, Deus  Isaiah 12:2-6'
    Output: 'The First Song of Isaiah  Ecce, Deus'
    (We keep the Latin subtitle as part of the canonical name.)
    """
    # Strip leading number
    raw = _NUMERIC_PREFIX_RE.sub("", raw).strip()
    # Trim trailing scripture citation: ≥3 whitespace chars then a book-name start
    raw = re.sub(r"\s{3,}[A-Z1-9][^\s].*$", "", raw).strip()
    # Normalise remaining internal whitespace runs (NBSP clusters → single space)
    raw = _WS_RE.sub(" ", raw).strip()
    return raw


def _clean_strong_heading(raw: str) -> str:
    """Strip trailing scripture citation/psalm number from a strong-text heading.

    Input:  'Venite   Psalm 95:1-7 '
    Output: 'Venite'
    """
    # Trim trailing citation starting with at least 2 spaces
    raw = re.sub(r"\s{2,}[A-Z1-9P][^\s].*$", "", raw).strip()
    return raw


def _detect_season(text: str) -> str | None:
    """If text starts with a known season name, return it; else None."""
    for season in _SEASON_NAMES:
        if text.startswith(season) and (
            len(text) == len(season) or not text[len(season)].isalpha()
        ):
            return season
    return None


def _classify_kind(name: str, body_text: str) -> KindLiteral:
    """Determine the semantic kind for a unit."""
    name_lower = name.lower()
    body_lower = body_text.lower()

    # Creed
    if "creed" in name_lower:
        return "creed"

    # Canticle
    if any(k in name_lower for k in _CANTICLE_KEYWORDS):
        return "canticle"
    # Canticle by "Song of" pattern
    if re.search(r"\bsong of\b", name_lower):
        return "canticle"
    if re.search(r"\bsong to\b", name_lower):
        return "canticle"

    # Psalm ref placeholder
    if "psalm or psalms appointed" in name_lower:
        return "psalm_ref"

    # Psalm heading (psalm number reference)
    if re.match(r"^psalm \d+", name_lower):
        return "canticle"

    # Seasonal sentence
    if name_lower.startswith("opening sentence"):
        return "seasonal_sentence"

    # Prayer (contains Amen)
    if "amen" in body_lower:
        return "prayer"

    # Suffrage (V./R. pattern or sets labelled A/B)
    if re.search(r"\bv\.\s+.*\br\.\s+", body_text, re.DOTALL | re.IGNORECASE):
        return "suffrage"
    if re.fullmatch(r"[AB]", name.strip()):
        return "suffrage"

    # Versicle/response (contains paired Officiant/People exchange)
    if re.search(r"(officiant|people)\b", body_lower):
        return "versicle_response"

    # Short body → intro
    if len(body_text) < 200:
        return "intro"

    return "rubric_block"


def _infer_office_and_rite(filename: str) -> tuple[OfficeLiteral, RiteLiteral]:
    """Derive office and rite from the basename."""
    stem = Path(filename).name.lower()
    mapping: dict[str, tuple[OfficeLiteral, RiteLiteral]] = {
        "mp1.html": ("morning_prayer", "rite_i"),
        "mp2.html": ("morning_prayer", "rite_ii"),
        "ep1.html": ("evening_prayer", "rite_i"),
        "ep2.html": ("evening_prayer", "rite_ii"),
        "compline.html": ("compline", "none"),
        "noonday.html": ("noonday", "none"),
        "devotion.html": ("daily_devotions", "none"),
        "devotion2.html": ("daily_devotions", "none"),
        "canticle.html": ("canticle", "both"),
        "evening.html": ("evening_prayer", "none"),
        "litany.html": ("great_litany", "none"),
    }
    if stem in mapping:
        return mapping[stem]
    logger.warning("Cannot determine office from %s; defaulting", filename)
    return ("morning_prayer", "none")


def _is_toc_file(source_file: str, html: str) -> bool:
    """Return True if this file should be skipped."""
    basename = Path(source_file).name
    if basename in _TOC_FILENAMES:
        return True
    return bool(len(html) < 1024 and "<strong>" not in html)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass
class _UnitBuilder:
    """Mutable accumulator for one office unit."""

    name: str = ""
    source_anchor: str | None = None
    body_parts: list[str] = field(default_factory=list)
    rubrics: list[str] = field(default_factory=list)
    page_number: int | None = None
    raw_season: str | None = None
    is_collect_style: bool = False  # True when started from a plain-text collect heading

    @property
    def body_text(self) -> str:
        joined = " ".join(self.body_parts)
        return _WS_RE.sub(" ", joined).strip()


# ---------------------------------------------------------------------------
# Parser states
# ---------------------------------------------------------------------------
_IDLE = "idle"
_IN_UNIT = "in_unit"
_AFTER_COLLECT_HEADING = "after_collect_heading"  # saw "A Collect for X", next p is body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_daily_office_file(path: Path) -> list[ParsedOffice]:
    """Parse one cached BCP 1979 Daily Office HTML file.

    Args:
        path: Path to the HTML file on disk.

    Returns:
        Ordered list of ParsedOffice units; [] for ToC / skip files.
    """
    html = path.read_text(encoding="utf-8", errors="replace")
    source_file = str(path)

    if _is_toc_file(path.name, html):
        return []

    office, rite = _infer_office_and_rite(path.name)
    soup = BeautifulSoup(html, "lxml")
    body_el = soup.find("body")
    if body_el is None:
        return []

    results: list[ParsedOffice] = []
    current: _UnitBuilder | None = None
    current_page: int | None = None

    def _emit(builder: _UnitBuilder) -> None:
        if not builder.name:
            return
        body_text = builder.body_text
        kind = _classify_kind(builder.name, body_text)
        slug = _slugify(builder.name)
        raw_meta: dict[str, Any] = {
            "office": office,
            "rite": rite,
            "page_number": builder.page_number,
            "source_anchor": builder.source_anchor,
            "source_file": source_file,
        }
        if builder.raw_season:
            raw_meta["season"] = builder.raw_season
        results.append(
            ParsedOffice(
                slug=slug,
                name=builder.name,
                rite=rite,
                office=office,
                kind=kind,
                body_text=body_text,
                rubrics=tuple(builder.rubrics),
                source_file=source_file,
                source_anchor=builder.source_anchor,
                page_number=builder.page_number,
                canonical_id=slug,
                raw_metadata=raw_meta,
            )
        )

    # Build a flat list of elements for lookahead
    elements = [el for el in body_el.children if isinstance(el, Tag)]
    n = len(elements)
    state = _IDLE

    i = 0
    while i < n:
        el = elements[i]
        tag_name = el.name

        # ----------------------------------------------------------------
        # Page markers
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_page_marker(el):
            page_num = _extract_page_number(el)
            if page_num is not None:
                current_page = page_num
                if current is not None and current.page_number is None:
                    current.page_number = current_page
            i += 1
            continue

        # ----------------------------------------------------------------
        # Skip
        # ----------------------------------------------------------------
        if tag_name in ("hr", "br"):
            i += 1
            continue

        if tag_name == "p" and _is_skip_class(el):
            i += 1
            continue

        if tag_name == "h1":
            # h1 is the page title (e.g. "An Order for Compline").
            # We use it to open an initial unit only for offices that have no
            # <strong>-headed units at the very start (great_litany, compline,
            # noonday).  For morning/evening prayer the first real units are
            # opened by <strong> headings or id= anchors.
            h1_text = _WS_RE.sub(" ", el.get_text()).strip()
            if h1_text and office in ("great_litany", "compline", "noonday", "evening_prayer"):
                if current is not None:
                    _emit(current)
                current = _UnitBuilder(name=h1_text, page_number=current_page)
                state = _IN_UNIT
            i += 1
            continue

        if tag_name in ("h2", "h3", "h4"):
            # h2/h3 in Litany.html introduce sub-sections.
            h_text = _WS_RE.sub(" ", el.get_text()).strip()
            if h_text and office == "great_litany":
                if current is not None:
                    _emit(current)
                current = _UnitBuilder(name=h_text, page_number=current_page)
                state = _IN_UNIT
            # For other offices, section headings don't emit units.
            i += 1
            continue

        # ----------------------------------------------------------------
        # Rubrics
        # ----------------------------------------------------------------
        if tag_name == "p" and _is_rubric(el):
            rubric_text = _paragraph_text(el)
            if rubric_text and current is not None:
                current.rubrics.append(rubric_text)
            i += 1
            continue

        # ----------------------------------------------------------------
        # Tables
        # ----------------------------------------------------------------
        if tag_name == "table":
            # Case 1: table with id= attribute → treat as named unit anchor.
            # Use the id value as the heading name (the table text is the body).
            tbl_id: str | None = el.get("id")  # type: ignore[assignment]
            if tbl_id:
                tbl_text = _paragraph_text(el)
                # Use a humanised version of the id as the name
                id_name = tbl_id.replace("_", " ").replace("-", " ").title()
                if current is not None:
                    _emit(current)
                current = _UnitBuilder(
                    name=id_name,
                    source_anchor=tbl_id,
                    page_number=current_page,
                )
                if tbl_text:
                    current.body_parts.append(tbl_text)
                state = _IN_UNIT
                i += 1
                continue

            # Case 2: gentable with a <strong> child → psalm/canticle heading
            css_set = set(_css_classes(el))
            if "gentable" in css_set and _has_strong(el):
                strong_text_val = _strong_text(el)
                # Extract just the heading (before the verse text)
                full_table_text = _paragraph_text(el)
                # The heading is the strong text; clean it
                heading = _clean_strong_heading(strong_text_val)
                if not heading:
                    heading = strong_text_val
                # Body is the rest of the table text after the heading
                body_after = full_table_text[len(strong_text_val):].strip() if strong_text_val in full_table_text else full_table_text
                if current is not None:
                    _emit(current)
                current = _UnitBuilder(
                    name=heading,
                    page_number=current_page,
                )
                if body_after:
                    current.body_parts.append(body_after)
                state = _IN_UNIT
                i += 1
                continue

            # Case 3: continuation table (no id, no strong heading)
            text = _paragraph_text(el)
            if text and current is not None:
                current.body_parts.append(text)
            i += 1
            continue

        # ----------------------------------------------------------------
        # <p> with <strong> — primary unit heading
        # ----------------------------------------------------------------
        if tag_name == "p" and _has_strong(el):
            if current is not None:
                _emit(current)
            name = _extract_heading_from_p(el)
            if not name:
                # Fallback: use full paragraph text
                name = _full_p_text(el)
            anchor: str | None = el.get("id")  # type: ignore[assignment]
            current = _UnitBuilder(
                name=name,
                source_anchor=anchor,
                page_number=current_page,
            )
            state = _IN_UNIT
            i += 1
            continue

        # ----------------------------------------------------------------
        # <p id="..."> without <strong> — named section anchor
        # ----------------------------------------------------------------
        p_id: str | None = el.get("id") if tag_name == "p" else None  # type: ignore[assignment]

        if tag_name == "p" and p_id and not _has_strong(el):
            text = _full_p_text(el)
            if text:
                if current is not None:
                    _emit(current)
                current = _UnitBuilder(
                    name=text,
                    source_anchor=p_id,
                    page_number=current_page,
                )
                state = _IN_UNIT
            i += 1
            continue

        # ----------------------------------------------------------------
        # Plain <p> without <strong>, without id
        # ----------------------------------------------------------------
        if tag_name == "p":
            text = _full_p_text(el)
            if not text:
                i += 1
                continue

            # --- Seasonal sentence detection ---
            season = _detect_season(text)
            if season is not None:
                if current is not None:
                    _emit(current)
                sentence_body = text[len(season):].strip()
                # Some files (mp2) put the season name alone on one <p>
                # and the scripture sentence on the next plain <p>.
                # Look ahead and absorb it if the body is empty.
                if not sentence_body:
                    for lookahead in range(i + 1, min(n, i + 4)):
                        nxt = elements[lookahead]
                        if nxt.name in ("hr", "br"):
                            continue
                        if nxt.name == "p" and (_is_page_marker(nxt) or _is_rubric(nxt) or _is_skip_class(nxt)):
                            continue
                        if nxt.name == "p":
                            sentence_body = _full_p_text(nxt)
                            i = lookahead  # advance past the consumed paragraph
                        break
                current = _UnitBuilder(
                    name=f"Opening Sentence ({season})",
                    page_number=current_page,
                    raw_season=season,
                )
                if sentence_body:
                    current.body_parts.append(sentence_body)
                # Seasonal sentences are single units; emit immediately
                _emit(current)
                current = None
                state = _IDLE
                i += 1
                continue

            # --- Collect-style heading detection ---
            if _is_collect_heading(text, elements, i):
                if current is not None:
                    _emit(current)
                current = _UnitBuilder(
                    name=text,
                    page_number=current_page,
                    is_collect_style=True,
                )
                state = _AFTER_COLLECT_HEADING
                i += 1
                continue

            # --- Body continuation ---
            if state == _AFTER_COLLECT_HEADING and current is not None:
                # The very first body paragraph for a collect-style heading
                current.body_parts.append(text)
                state = _IN_UNIT
                i += 1
                continue

            if current is not None:
                current.body_parts.append(text)
            # Orphan text before any heading → skip

        i += 1

    # Finalise last unit
    if current is not None:
        _emit(current)

    return results


def _is_collect_heading(text: str, elements: list[Tag], idx: int) -> bool:
    """Return True if text looks like a collect/prayer heading.

    Criteria (both required):
    1. Text matches _COLLECT_HEADING_RE, OR is a short label (≤60 chars)
       that looks like a title (capitalised, no sentence-ending punctuation,
       not an office direction like "Officiant and People").
    2. The next substantive non-rubric, non-page-marker <p> in elements
       is longer (≥50 chars) — i.e. it looks like a prayer body.
    """
    if not text or len(text) > 80:
        return False

    is_collect_pattern = bool(_COLLECT_HEADING_RE.match(text))
    # Additional known-label patterns that create unit boundaries in specific files
    # (e.g. "Festivals of Saints" in evening.html, "The Collect" in devotion).
    is_known_label = bool(
        re.match(
            r"^(Festivals of Saints|The Collect of the Day)\b",
            text,
        )
    )

    if not (is_collect_pattern or is_known_label):
        return False

    # Look ahead for a substantive body paragraph (≥50 chars).
    # Skip over short interstitial paragraphs like "Officiant and People".
    for j in range(idx + 1, min(len(elements), idx + 8)):
        nxt = elements[j]
        if nxt.name in ("hr", "br"):
            continue
        if nxt.name == "p" and _is_page_marker(nxt):
            continue
        if nxt.name == "p" and _is_rubric(nxt):
            continue
        if nxt.name == "p" and _is_skip_class(nxt):
            continue
        if nxt.name == "p":
            next_text = _full_p_text(nxt)
            if len(next_text) < 50:
                # Short interstitial (e.g. "Officiant and People") — keep looking
                continue
            return True
        # Hit a table or <p strong> → not a collect heading
        return False
    return False
