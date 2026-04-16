"""Video filename parser for Phase 5b (movie and TV show ingest).

parse(filename, is_tv) -> dict

Parses torrent-style filenames using parse-torrent-title (PTN) as the primary
engine, with pre- and post-processing for common edge cases:
  - Anime bracket prefixes: "[Kinomoto] Cardcaptor Sakura ..."
  - Multi-season ranges: "S01-S08" → season list, we take the first
  - Bare simple filenames: "101 Dalmatians.avi"
  - Dot-separated names: "Blood.of.Zeus.S01.COMPLETE.720p..."

Raises ValueError with the raw filename if no title can be extracted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import PTN  # type: ignore[import-untyped]
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "parse-torrent-title is required. Install with: pip install parse-torrent-title"
    ) from exc

# ---------------------------------------------------------------------------
# Pre-processing patterns
# ---------------------------------------------------------------------------

# Strip leading anime-style group tags: "[Group Name] Title ..."
_LEADING_BRACKET_RE = re.compile(r"^\s*\[[^\]]+\]\s*")

# Strip file extension before parsing (PTN handles .mkv etc but inconsistently)
_VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg", ".ts"}

# Detect dot-separated filenames (more dots than spaces in the title portion)
_DOT_SEPARATED_RE = re.compile(r"^[\w.]+\.(S\d{2}|19\d{2}|20\d{2})\.", re.IGNORECASE)


def parse(filename: str, *, is_tv: bool = False) -> dict[str, Any]:
    """Parse a video filename into structured metadata.

    Parameters
    ----------
    filename:
        The top-level directory name or filename (not a full path).
        Examples:
          "Andor (2022) Season 2 S02 (2160p HDR DSNP WEB-DL x265)"
          "[Kinomoto] Cardcaptor Sakura [BD 1080p Dual-Audio]"
          "101 Dalmatians.avi"
    is_tv:
        Hint to the parser that this entry is from the TV Shows directory.
        Affects how season/episode fields are handled.

    Returns
    -------
    dict with keys:
        title (str): cleaned title
        year (int | None): release year
        season (int | None): season number (TV only; first if multi-season)
        episode (int | None): episode number (rarely populated for folder names)

    Raises
    ------
    ValueError
        If no title can be extracted from the filename.
    """
    raw = filename.strip()

    # Strip file extension if present
    p = Path(raw)
    if p.suffix.lower() in _VIDEO_SUFFIXES:
        raw = p.stem

    # Strip leading group/fansub bracket: "[Kinomoto] Title ..."
    cleaned = _LEADING_BRACKET_RE.sub("", raw).strip()

    # Run PTN parser
    parsed: dict[str, Any] = PTN.parse(cleaned)

    title: str | None = parsed.get("title")

    # PTN sometimes returns empty title on simple clean names like "Bluey"
    # or when the whole string is a title with no release markers
    if not title:
        # Fall back: use the cleaned string up to the first codec/quality marker
        title = _extract_title_fallback(cleaned)

    if not title:
        raise ValueError(f"could not extract title from filename: {filename!r}")

    # Normalize title: collapse extra whitespace
    title = re.sub(r"\s+", " ", title).strip()

    if not title:
        raise ValueError(f"could not extract title from filename: {filename!r}")

    # Year
    year: int | None = parsed.get("year")
    if isinstance(year, list):
        year = year[0] if year else None

    # Season: PTN may return int or list (for "S01-S08" style ranges)
    season_raw = parsed.get("season")
    season: int | None = None
    if season_raw is not None:
        if isinstance(season_raw, list):
            season = int(season_raw[0]) if season_raw else None
        else:
            season = int(season_raw)

    # Episode
    episode_raw = parsed.get("episode")
    episode: int | None = None
    if episode_raw is not None:
        if isinstance(episode_raw, list):
            episode = int(episode_raw[0]) if episode_raw else None
        else:
            episode = int(episode_raw)

    return {
        "title": title,
        "year": year,
        "season": season,
        "episode": episode,
    }


# ---------------------------------------------------------------------------
# Fallback title extraction
# ---------------------------------------------------------------------------

# Quality/codec/resolution markers that signal "end of title"
_NOISE_MARKERS = re.compile(
    r"""
    \b(
        \d{3,4}p           # resolution: 720p, 1080p, 2160p
        | BluRay | BDRip | BRRip | WEB-DL | WEBRip | HDTV | DVDRip
        | x264 | x265 | HEVC | H\.264 | H\.265 | AVC | XVID | XviD
        | DDP | DTS | AAC | AC3 | MP3 | FLAC
        | HDR | SDR | UHD | Atmos
        | S\d{2}E\d{2}     # episode code SxxExx
        | S\d{2}\b         # season code Sxx
        | 19\d{2} | 20\d{2}  # year
        | COMPLETE | REPACK | PROPER | EXTENDED | THEATRICAL
        | MULTi | MULTI
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_title_fallback(s: str) -> str | None:
    """Extract title from a string by cutting at the first quality/codec marker."""
    m = _NOISE_MARKERS.search(s)
    if m:
        candidate = s[: m.start()].strip(" .-_")
        return candidate if candidate else None
    # No markers found — the whole string is probably the title
    return s.strip() or None
