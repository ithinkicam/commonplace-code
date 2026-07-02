"""Podcast URL ingest handler.

``handle_podcast_ingest(payload, conn)`` is the worker handler for
``ingest_podcast`` jobs.

Behaviour
---------
1. Validate and normalize the podcast episode URL (strip tracking params).
2. Try to discover an RSS feed for the podcast:
   a. Fetch the episode page, look for ``<link rel="alternate"
      type="application/rss+xml">`` in the HTML.
   b. For Apple Podcasts URLs, use the iTunes lookup API to find ``feedUrl``.
3. If RSS feed found, parse it with ``feedparser``, match the episode entry,
   and look for a ``<podcast:transcript>`` tag.  Fetch the transcript
   (SRT, VTT, or plain text) and clean it.
4. If no RSS transcript found, download the audio via yt-dlp and run through
   the shared ``transcription.transcribe()`` module (Whisper-medium).
5. Extract metadata (show title, episode title, published date, duration,
   description) from RSS or best-effort from the page.
6. Write a vault file atomically (YAML frontmatter + markdown body).
7. Optionally invoke ``summarize_capture`` skill for long content (>2000 words).
8. Embed the transcript (or summary text if summarized).

Typed exceptions
----------------
- :class:`PodcastError` — base.
- :class:`PodcastFetchError` — network failure, invalid URL.
- :class:`PodcastTranscriptionError` — both RSS transcript and Whisper failed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from commonplace_worker.binaries import resolve_ytdlp
from commonplace_worker.checkpoints import for_payload, stage_cache_dir
from commonplace_worker.claude_skill import SkillFailure, SkillTimeout, run_skill
from commonplace_worker.errors import RetryableHandlerError
from commonplace_worker.frontmatter import render_embed_header, slugify, yaml_escape
from commonplace_worker.vault_io import atomic_write_text, vault_root

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class PodcastError(Exception):
    """Base class for podcast handler errors."""


class PodcastFetchError(PodcastError):
    """Network failure, invalid URL, or fetch error."""


class PodcastTranscriptionError(PodcastError):
    """Neither RSS transcript nor Whisper produced a usable transcript."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class PodcastMetadata:
    """Metadata extracted from RSS or page scraping."""

    episode_title: str | None = None
    show_title: str | None = None
    published_at: str | None = None
    duration_s: float = 0.0
    description: str | None = None


@dataclass
class TranscriptResult:
    """Result from transcript discovery or Whisper transcription."""

    text: str
    source: str  # "rss_transcript" | "whisper"


# ---------------------------------------------------------------------------
# Tracking params to strip from URLs
# ---------------------------------------------------------------------------

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "referrer", "mc_cid", "mc_eid",
})


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


def _canonicalize_url(url: str) -> str:
    """Normalize a podcast URL: http(s) only, drop fragment, strip tracking params.

    Raises ``PodcastFetchError`` if the scheme is not http/https.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PodcastFetchError(
            f"unsupported URL scheme {parsed.scheme!r}: {url!r} "
            "(only http/https are accepted)"
        )
    if not parsed.netloc:
        raise PodcastFetchError(f"URL missing hostname: {url!r}")

    # Strip tracking query params
    qs = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    new_query = urlencode(filtered, doseq=True) if filtered else ""

    path = parsed.path or "/"
    cleaned = parsed._replace(fragment="", query=new_query, path=path)
    return urlunparse(cleaned)


# ---------------------------------------------------------------------------
# RSS feed discovery
# ---------------------------------------------------------------------------


def _find_rss_link_in_html(html: str) -> str | None:
    """Extract RSS feed URL from <link rel="alternate" type="application/rss+xml"> tag."""
    # Simple regex to find RSS links in HTML head
    pattern = re.compile(
        r'<link[^>]+type=["\']application/rss\+xml["\'][^>]*href=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if match:
        return match.group(1)
    # Try reversed attribute order
    pattern2 = re.compile(
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]*type=["\']application/rss\+xml["\']',
        re.IGNORECASE,
    )
    match2 = pattern2.search(html)
    if match2:
        return match2.group(1)
    return None


def _apple_podcast_id(url: str) -> str | None:
    """Extract Apple Podcasts ID from URL, if applicable."""
    parsed = urlparse(url)
    if "apple.com" not in (parsed.hostname or ""):
        return None
    # URL like: https://podcasts.apple.com/.../id1234567890
    match = re.search(r"/id(\d+)", parsed.path)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Pocket Casts resolution (pca.st / pocketcasts.com share links)
# ---------------------------------------------------------------------------
#
# Pocket Casts' web page is a client-rendered React SPA: the audio URL is
# never present in the HTML the server returns, so yt-dlp can't extract it
# and there's no <link rel="alternate" type="application/rss+xml"> to
# discover the feed. Their private API requires authentication.
#
# Workaround: use what IS in the HTML — the show slug in the URL path and
# the episode title in og:title / twitter:title meta tags — to resolve to
# the podcast's public RSS feed via iTunes Search, then match the episode
# by normalised title and return the <enclosure> URL.
#
# The resolver is called as a pre-step in ``ingest_podcast_url``; the rest
# of the handler flow (RSS transcript discovery, Whisper fallback, vault
# write, embed) is unchanged.


def _is_pocketcasts_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"pca.st", "www.pca.st", "pocketcasts.com", "www.pocketcasts.com"}


def _find_meta_content(html: str, attr: str, value: str) -> str | None:
    """Return the ``content`` of a ``<meta>`` tag matching attr=value.

    Tolerant of attribute order and extra attributes like ``data-rh``,
    which Pocket Casts adds. Returns None if no match.
    """
    import html as _html  # stdlib html module; aliased to avoid shadowing the arg

    pattern = re.compile(
        rf'<meta\b[^>]*\b{re.escape(attr)}\s*=\s*"{re.escape(value)}"[^>]*>',
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if not m:
        return None
    content_m = re.search(r'\bcontent\s*=\s*"([^"]*)"', m.group(0))
    if content_m is None:
        return None
    return _html.unescape(content_m.group(1)).strip() or None


def _normalise_episode_title(title: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace for fuzzy match."""
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _itunes_search_feed_url(query: str) -> str | None:
    """Return the top iTunes feed URL matching ``query`` (podcast search)."""
    import httpx

    try:
        resp = httpx.get(
            "https://itunes.apple.com/search",
            params={
                "term": query,
                "media": "podcast",
                "entity": "podcast",
                "limit": 3,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("iTunes search failed for %r: %s", query, exc)
        return None
    results = data.get("results") or []
    for item in results:
        feed = item.get("feedUrl")
        if feed:
            return str(feed)
    return None


def _find_enclosure_in_rss(rss_text: str, episode_title: str) -> str | None:
    """Find the <enclosure url=...> whose <title> matches ``episode_title``.

    Match is case-insensitive and punctuation-insensitive; falls back to
    substring containment if an exact normalised match is not found.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(rss_text)
    except ET.ParseError as exc:
        logger.info("RSS parse failed: %s", exc)
        return None

    target = _normalise_episode_title(episode_title)
    fuzzy_candidate: str | None = None

    for item in root.iter("item"):
        title_el = item.find("title")
        enc_el = item.find("enclosure")
        if title_el is None or enc_el is None:
            continue
        item_title = (title_el.text or "").strip()
        item_norm = _normalise_episode_title(item_title)
        enc_url = enc_el.get("url")
        if not enc_url:
            continue
        if item_norm == target:
            return enc_url
        # Tolerant fallback: target is a substring of the item title or
        # vice versa. Protects against prefixes like "1. " being absent
        # in one source but present in the other.
        if fuzzy_candidate is None and (target in item_norm or item_norm in target):
            fuzzy_candidate = enc_url

    return fuzzy_candidate


def resolve_pocketcasts_url(url: str) -> str | None:
    """Resolve a pca.st / pocketcasts.com share URL to a direct audio URL.

    Returns the enclosure URL on success, or ``None`` if any step fails —
    caller decides whether to raise or continue. All network calls are
    best-effort; a failure here should NOT retry the whole podcast job,
    since the upstream Pocket Casts page and iTunes catalog aren't
    going to change within the retry window.
    """
    import httpx

    try:
        resp = httpx.get(url, follow_redirects=True, timeout=15)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.info("pocketcasts: fetch failed for %s: %s", url, exc)
        return None

    final_url = str(resp.url)
    parsed = urlparse(final_url)
    parts = [p for p in parsed.path.split("/") if p]
    # Expected path: /podcast/<show-slug>/<show-uuid>/<ep-slug>/<ep-uuid>
    if len(parts) < 2 or parts[0] != "podcast":
        logger.info("pocketcasts: unexpected redirect path: %s", final_url)
        return None
    show_slug = parts[1]

    episode_title = (
        _find_meta_content(resp.text, "name", "twitter:title")
        or _find_meta_content(resp.text, "property", "og:title")
    )
    if not episode_title:
        logger.info("pocketcasts: no episode title in %s", final_url)
        return None

    feed_url = _itunes_search_feed_url(show_slug.replace("-", " "))
    if not feed_url:
        logger.info(
            "pocketcasts: no iTunes feed for show %r (episode %r)",
            show_slug, episode_title,
        )
        return None

    try:
        feed_resp = httpx.get(feed_url, follow_redirects=True, timeout=20)
        feed_resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.info("pocketcasts: RSS fetch failed for %s: %s", feed_url, exc)
        return None

    enclosure = _find_enclosure_in_rss(feed_resp.text, episode_title)
    if not enclosure:
        logger.info(
            "pocketcasts: episode %r not found in feed %s",
            episode_title, feed_url,
        )
        return None

    logger.info(
        "pocketcasts: resolved %s -> %s (via %s)", url, enclosure, feed_url
    )
    return enclosure


# ---------------------------------------------------------------------------
# Transcript format cleaning
# ---------------------------------------------------------------------------


def _clean_vtt(vtt_text: str) -> str:
    """Strip VTT headers, timestamps, and tags from caption text."""
    lines = vtt_text.splitlines()
    text_lines: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT"):
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if line.startswith("NOTE"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->", line):
            continue
        cleaned = re.sub(r"<[^>]+>", "", line)
        if cleaned.strip():
            text_lines.append(cleaned.strip())

    deduped: list[str] = []
    for line in text_lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return " ".join(deduped)


def _clean_srt(srt_text: str) -> str:
    """Strip SRT sequence numbers, timestamps, and tags from caption text."""
    lines = srt_text.splitlines()
    text_lines: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip sequence numbers (bare integers)
        if re.match(r"^\d+$", line):
            continue
        # Skip timestamp lines: 00:00:01,000 --> 00:00:04,000
        if re.match(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->", line):
            continue
        # Strip HTML-style tags
        cleaned = re.sub(r"<[^>]+>", "", line)
        if cleaned.strip():
            text_lines.append(cleaned.strip())

    deduped: list[str] = []
    for line in text_lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return " ".join(deduped)


def _clean_transcript(text: str, content_type: str = "") -> str:
    """Clean a transcript based on its detected format.

    Detects VTT, SRT, or plain text and cleans accordingly.
    """
    content_type_lower = content_type.lower()
    stripped = text.strip()

    if "vtt" in content_type_lower or stripped.startswith("WEBVTT"):
        return _clean_vtt(text)
    if "srt" in content_type_lower or re.match(r"^\d+\r?\n\d{2}:\d{2}:\d{2},\d{3}", stripped):
        return _clean_srt(text)
    # Plain text — just normalize whitespace
    return " ".join(stripped.split())


# ---------------------------------------------------------------------------
# Default callables (production implementations)
# ---------------------------------------------------------------------------


class _DefaultFetcher:
    """Wraps HTTP fetching. Replaced by _fetcher in tests."""

    def fetch_page(self, url: str) -> str:
        """Fetch a URL and return the HTML body."""
        import httpx

        try:
            response = httpx.get(url, follow_redirects=True, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            raise PodcastFetchError(f"failed to fetch {url!r}: {exc}") from exc
        return response.text

    def fetch_transcript(self, url: str) -> tuple[str, str]:
        """Fetch a transcript URL. Returns (text, content_type)."""
        import httpx

        try:
            response = httpx.get(url, follow_redirects=True, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            raise PodcastFetchError(
                f"failed to fetch transcript at {url!r}: {exc}"
            ) from exc
        ct = response.headers.get("content-type", "")
        return response.text, ct

    def fetch_apple_feed_url(self, podcast_id: str) -> str | None:
        """Use iTunes lookup API to get the RSS feed URL for an Apple Podcast."""
        import httpx

        try:
            response = httpx.get(
                f"https://itunes.apple.com/lookup?id={podcast_id}&entity=podcast",
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.info(
                "iTunes lookup failed for podcast_id=%s: %s — falling back to URL-only flow",
                podcast_id, exc,
            )
            return None

        results = data.get("results", [])
        if results:
            return results[0].get("feedUrl")  # type: ignore[no-any-return]
        return None

    def download_audio(self, url: str, output_path: Path) -> Path:
        """Download podcast audio via yt-dlp for Whisper transcription."""
        return _download_audio(url, output_path)


class _DefaultRSSParser:
    """Wraps RSS parsing via feedparser. Replaced by _rss_parser in tests."""

    def parse_feed(self, feed_url: str) -> Any:
        """Parse an RSS feed URL and return the feedparser result."""
        import feedparser  # type: ignore[import-untyped]

        return feedparser.parse(feed_url)


def _default_transcriber(audio_path: Path) -> TranscriptResult:
    """Transcribe via the shared transcription module."""
    from commonplace_worker.transcription import transcribe

    result = transcribe(audio_path, model_size="medium", language="en")
    return TranscriptResult(text=result.text, source="whisper")


def _default_summarizer(text: str, title: str, url: str) -> dict[str, Any] | None:
    """Invoke the summarize_capture skill via the shared claude_skill helper.

    Returns parsed summary dict or None if summarization is not needed
    or fails. Summarization is best-effort — any failure (timeout, missing
    binary, non-zero exit, parse error) is logged and yields ``None``.
    """
    from skills.summarize_capture.parser import (
        CaptureSummary,
        parse,
        should_summarize,
        verify_quotes,
    )

    if not should_summarize(text):
        return None

    input_json = json.dumps({
        "source_kind": "podcast",
        "title": title,
        "url": url,
        "text": text,
    })

    skill_path = (
        Path(__file__).resolve().parent.parent.parent
        / "skills" / "summarize_capture" / "SKILL.md"
    )

    try:
        result = run_skill(
            skill_md=skill_path,
            payload=input_json,
            model="haiku",
            timeout_s=120,
        )
    except SkillTimeout:
        logger.warning("summarize_capture skill invocation failed")
        return None
    except SkillFailure as exc:
        logger.warning("summarize_capture failed: %s", exc)
        return None

    try:
        summary: CaptureSummary = parse(result.stdout)
    except Exception:
        logger.warning("summarize_capture output parse failed", exc_info=True)
        return None

    bad_quotes = verify_quotes(summary, text)
    if bad_quotes:
        logger.warning(
            "summarize_capture fabricated %d quotes, dropping them", len(bad_quotes)
        )
        summary.quotes = [q for q in summary.quotes if q not in bad_quotes]

    return {
        "description": summary.description,
        "key_points": summary.key_points,
        "quotes": summary.quotes,
    }


# ---------------------------------------------------------------------------
# RSS episode matching
# ---------------------------------------------------------------------------


def _match_episode_in_feed(
    feed: Any, episode_url: str
) -> dict[str, Any] | None:
    """Find the feed entry matching the given episode URL.

    Checks link, id, and enclosure URLs against the canonical episode URL.
    Returns the matched entry dict or None.
    """
    canonical_parsed = urlparse(episode_url)
    canonical_path = canonical_parsed.path.rstrip("/")

    for entry in getattr(feed, "entries", []):
        # Check entry link
        entry_link = getattr(entry, "link", "") or ""
        if entry_link:
            entry_parsed = urlparse(entry_link)
            if entry_parsed.path.rstrip("/") == canonical_path:
                return entry  # type: ignore[no-any-return]

        # Check entry id
        entry_id = getattr(entry, "id", "") or ""
        if entry_id and episode_url in entry_id:
            return entry  # type: ignore[no-any-return]

        # Check enclosure URLs
        for link in getattr(entry, "links", []):
            href = link.get("href", "")
            if href and episode_url in href:
                return entry  # type: ignore[no-any-return]

    return None


def _find_transcript_url_in_entry(entry: Any) -> tuple[str, str] | None:
    """Look for <podcast:transcript> in an RSS entry.

    feedparser exposes namespace-tagged elements under entry keys. The
    podcast namespace (https://podcastindex.org/namespace/1.0) tags appear
    as ``podcast_transcript`` in feedparser's normalized namespace handling.

    Returns (transcript_url, type) or None.
    """
    # feedparser stores podcast:transcript tags in entry.get("podcast_transcript")
    # or as sub-elements. Try multiple access patterns.

    # Pattern 1: feedparser may expose as a list of dicts
    transcripts = getattr(entry, "podcast_transcript", None)
    if transcripts:
        if isinstance(transcripts, list):
            for t in transcripts:
                url = t.get("url") or t.get("href", "")
                ttype = t.get("type", "text/plain")
                if url:
                    return url, ttype
        elif isinstance(transcripts, dict):
            url = transcripts.get("url") or transcripts.get("href", "")
            ttype = transcripts.get("type", "text/plain")
            if url:
                return url, ttype

    # Pattern 2: check in links for rel="transcript"
    for link in getattr(entry, "links", []):
        if link.get("rel") == "transcript":
            return link.get("href", ""), link.get("type", "text/plain")

    # Pattern 3: raw XML tags stored by feedparser for unknown namespaces
    # feedparser may store them under entry["tags"] or similar structures
    # For the podcast namespace, tags may appear with various prefixes
    if hasattr(entry, "get"):
        for key in entry:
            if "transcript" in str(key).lower() and key != "podcast_transcript":
                val = entry[key]
                if isinstance(val, str) and val.startswith("http"):
                    return val, "text/plain"
                if isinstance(val, dict):
                    url = val.get("url") or val.get("href", "")
                    if url:
                        return url, val.get("type", "text/plain")

    return None


def _extract_metadata_from_entry(entry: Any, feed: Any) -> PodcastMetadata:
    """Extract podcast metadata from a feedparser entry and feed."""
    episode_title = getattr(entry, "title", None)
    show_title = getattr(feed.feed, "title", None) if hasattr(feed, "feed") else None
    published_at = getattr(entry, "published", None)
    description = getattr(entry, "summary", None)

    # Duration from itunes:duration
    duration_s = 0.0
    itunes_duration = getattr(entry, "itunes_duration", None)
    if itunes_duration:
        duration_s = _parse_duration(str(itunes_duration))

    return PodcastMetadata(
        episode_title=episode_title,
        show_title=show_title,
        published_at=published_at,
        duration_s=duration_s,
        description=description,
    )


def _parse_duration(duration_str: str) -> float:
    """Parse an iTunes duration string (HH:MM:SS, MM:SS, or seconds) to float seconds."""
    duration_str = duration_str.strip()
    if not duration_str:
        return 0.0

    # Pure seconds
    if duration_str.isdigit():
        return float(duration_str)

    parts = duration_str.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(duration_str)
    except (ValueError, IndexError):
        return 0.0


# Suffixes that podcast host sites append to their <title> tag. Stripped so
# the episode title we store matches what the user would type if searching.
# Listed with each punctuation variant explicitly — matching by separator
# character alone would risk eating legitimate episode-title punctuation.
_TITLE_SITE_SUFFIXES: tuple[str, ...] = (
    " - Pocket Casts",
    " — Pocket Casts",
    " | Pocket Casts",
    " - Apple Podcasts",
    " — Apple Podcasts",
    " | Apple Podcasts",
    " - YouTube",
    " | YouTube",
    " - Overcast",
    " | Overcast",
    " - Spotify",
    " | Spotify",
)


def _extract_title_from_html(html_text: str) -> str | None:
    """Best-effort title extraction from HTML <title> tag.

    Unescapes HTML entities (``&amp;`` → ``&``) and strips well-known
    podcast-host site-name suffixes so the stored title matches what the
    user would actually type when searching. Conservative: only suffixes
    from a fixed list are stripped; episode titles that incidentally
    contain punctuation like " — Part 2" are left alone.
    """
    import html as _html

    match = re.search(r"<title[^>]*>([^<]+)</title>", html_text, re.IGNORECASE)
    if not match:
        return None
    title = _html.unescape(match.group(1)).strip()
    for suffix in _TITLE_SITE_SUFFIXES:
        if title.endswith(suffix):
            title = title[: -len(suffix)].rstrip()
            break
    return title or None


# ---------------------------------------------------------------------------
# Audio download via yt-dlp
# ---------------------------------------------------------------------------


def _download_audio(url: str, output_path: Path) -> Path:
    """Download podcast audio via yt-dlp for Whisper transcription."""
    try:
        result = subprocess.run(  # noqa: S603
            [
                resolve_ytdlp(),
                "-f", "bestaudio",
                "-x",
                "--audio-format", "wav",
                "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
                "--no-playlist",
                "-o", str(output_path.with_suffix(".%(ext)s")),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise PodcastFetchError(
            f"yt-dlp audio download failed for {url!r}: {exc}"
        ) from exc

    # Surface non-zero exit with the actual stderr so diagnostics don't
    # collapse to "did not produce audio file". Missing-wav is kept as a
    # secondary check in case yt-dlp returns 0 but the post-processor
    # silently skipped the ffmpeg conversion step.
    if result.returncode != 0:
        raise PodcastFetchError(
            f"yt-dlp exit {result.returncode} for {url!r}: "
            f"{result.stderr[:500].strip()}"
        )

    wav_path = output_path.with_suffix(".wav")
    if not wav_path.exists():
        raise PodcastFetchError(
            f"yt-dlp returncode=0 but no audio file produced at {wav_path}; "
            f"stderr: {result.stderr[:500].strip()}"
        )
    return wav_path


# ---------------------------------------------------------------------------
# Vault writing
# ---------------------------------------------------------------------------


def _write_vault_file(
    *,
    canonical_url: str,
    episode_title: str | None,
    show_title: str | None,
    published_at: str | None,
    duration_s: float,
    transcript_source: str,
    transcript_text: str,
    transcript_words: int,
    summarized: bool,
    summary: dict[str, Any] | None,
    fetched_at: datetime,
) -> Path:
    """Atomically write the transcript as a markdown file and return its path.

    Layout: ``<vault>/captures/YYYY/MM/<UTC-timestamp>-podcast-<slug>.md``.
    """
    root = vault_root()
    year = fetched_at.strftime("%Y")
    month = fetched_at.strftime("%m")
    out_dir = root / "captures" / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = fetched_at.strftime("%Y-%m-%dT%H%M%SZ")
    slug_src = episode_title or show_title or "episode"
    slug = slugify(slug_src, fallback="episode")
    filename = f"{ts}-podcast-{slug}.md"
    final_path = out_dir / filename

    content = _render_markdown(
        canonical_url=canonical_url,
        episode_title=episode_title,
        show_title=show_title,
        published_at=published_at,
        duration_s=duration_s,
        transcript_source=transcript_source,
        transcript_text=transcript_text,
        transcript_words=transcript_words,
        summarized=summarized,
        summary=summary,
        fetched_at=fetched_at,
    )

    atomic_write_text(final_path, content)
    return final_path


def _render_markdown(
    *,
    canonical_url: str,
    episode_title: str | None,
    show_title: str | None,
    published_at: str | None,
    duration_s: float,
    transcript_source: str,
    transcript_text: str,
    transcript_words: int,
    summarized: bool,
    summary: dict[str, Any] | None,
    fetched_at: datetime,
) -> str:
    """Return the full frontmatter + body string."""
    lines: list[str] = ["---", "source: podcast"]
    lines.append(f"url: {yaml_escape(canonical_url)}")
    if episode_title:
        lines.append(f"episode_title: {yaml_escape(episode_title)}")
    if show_title:
        lines.append(f"show_title: {yaml_escape(show_title)}")
    if published_at:
        lines.append(f"published_at: {yaml_escape(published_at)}")
    lines.append(f"duration_s: {duration_s:.0f}")
    lines.append(f"transcript_source: {transcript_source}")
    lines.append(f"transcript_words: {transcript_words}")
    lines.append(f"summarized: {'true' if summarized else 'false'}")
    lines.append(
        f"fetched_at: {yaml_escape(fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
    )
    lines.append("---")
    lines.append("")

    if summarized and summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(summary.get("description", ""))
        lines.append("")
        if summary.get("key_points"):
            lines.append("## Key points")
            lines.append("")
            for point in summary["key_points"]:
                lines.append(f"- {point}")
            lines.append("")
        if summary.get("quotes"):
            lines.append("## Quotes")
            lines.append("")
            for quote in summary["quotes"]:
                lines.append(f"> {quote}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Full transcript")
        lines.append("")

    lines.append(transcript_text.rstrip())
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_podcast_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _fetcher: Any | None = None,
    _rss_parser: Any | None = None,
    _transcriber: Any | None = None,
    _summarizer: Any | None = None,
    _embedder: Any | None = None,
) -> dict[str, Any]:
    """Worker handler for ``ingest_podcast`` jobs.

    Parameters
    ----------
    payload:
        ``{"url": str, "inbox_file": str | None}``
    conn:
        Open SQLite connection with migrations applied.
    _fetcher:
        Override for HTTP fetching (testing). Must have ``fetch_page``,
        ``fetch_transcript``, ``fetch_apple_feed_url`` methods.
    _rss_parser:
        Override for RSS parsing (testing). Must have ``parse_feed`` method.
    _transcriber:
        Override for Whisper transcription (testing). Callable taking
        ``audio_path: Path`` and returning ``TranscriptResult``.
    _summarizer:
        Override for summarize_capture skill (testing). Callable taking
        ``(text, title, url)`` and returning ``dict | None``.
    _embedder:
        Optional embedder override forwarded to ``pipeline.embed_document``.

    Returns
    -------
    dict with keys: ``document_id``, ``chunk_count``, ``elapsed_ms``,
    ``url``, ``episode_title``, ``show_title``, ``transcript_source``,
    ``transcript_words``, ``summarized``.
    """
    t0 = time.monotonic()

    url_raw = payload.get("url")
    if not isinstance(url_raw, str) or not url_raw.strip():
        raise ValueError(f"ingest_podcast payload missing 'url': {payload!r}")

    # Stage checkpointer (no-op when called outside the worker, e.g. tests).
    attempt_raw = payload.get("_attempt", 0)
    attempt = int(attempt_raw) if isinstance(attempt_raw, int) else 0
    ckpt = for_payload(conn, payload, attempt)
    job_id_raw = payload.get("_job_id")
    job_id = int(job_id_raw) if isinstance(job_id_raw, int) else None

    # Stage 1: canonicalize URL
    out = ckpt.get_output("url_canonicalized")
    if out and isinstance(out.get("canonical"), str):
        canonical = out["canonical"]
    else:
        ckpt.start("url_canonicalized")
        canonical = _canonicalize_url(url_raw.strip())
        ckpt.complete("url_canonicalized", {"canonical": canonical})

    # Fast path: if we already wrote the doc on a prior attempt, return success
    # without redoing any network / transcription work.
    written = ckpt.get_output("doc_written")
    if written and isinstance(written.get("document_id"), int):
        existing_id = int(written["document_id"])
        existing_row = conn.execute(
            "SELECT id FROM documents WHERE id = ?", (existing_id,)
        ).fetchone()
        if existing_row is not None:
            chunk_count_row = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
            ).fetchone()
            chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
            meta_out = ckpt.get_output("metadata_extracted") or {}
            transcript_out = ckpt.get_output("transcript_obtained") or {}
            summarized_out = ckpt.get_output("summarized") or {}
            summarized_flag = bool(summarized_out) and not summarized_out.get("skipped")
            transcript_words = 0
            text_path = transcript_out.get("text_path")
            if isinstance(text_path, str) and Path(text_path).exists():
                try:
                    transcript_words = len(Path(text_path).read_text().split())
                except OSError:
                    transcript_words = 0
            elapsed_ms = (time.monotonic() - t0) * 1000
            return {
                "document_id": existing_id,
                "chunk_count": chunk_count,
                "elapsed_ms": elapsed_ms,
                "url": canonical,
                "episode_title": meta_out.get("episode_title"),
                "show_title": meta_out.get("show_title"),
                "transcript_source": transcript_out.get("source", "unknown"),
                "transcript_words": transcript_words,
                "summarized": summarized_flag,
            }

    # Idempotency check by (content_type, source_id)
    existing = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'podcast' AND source_id = ?",
        (canonical,),
    ).fetchone()

    if existing is not None:
        existing_id = int(existing["id"])
        chunk_count_row = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
        ).fetchone()
        chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "podcast already ingested document_id=%d url=%s", existing_id, canonical
        )
        doc_row = conn.execute(
            "SELECT title FROM documents WHERE id = ?", (existing_id,)
        ).fetchone()
        return {
            "document_id": existing_id,
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "url": canonical,
            "episode_title": doc_row["title"] if doc_row else None,
            "show_title": None,
            "transcript_source": "unknown",
            "transcript_words": 0,
            "summarized": False,
        }

    # Set up callables
    fetcher = _fetcher if _fetcher is not None else _DefaultFetcher()
    rss_parser = _rss_parser if _rss_parser is not None else _DefaultRSSParser()
    transcriber = _transcriber if _transcriber is not None else _default_transcriber
    summarizer = _summarizer if _summarizer is not None else _default_summarizer

    # Stage 2: obtain transcript (RSS or Whisper fallback).
    transcript_result, metadata = _obtain_transcript(
        canonical=canonical,
        fetcher=fetcher,
        rss_parser=rss_parser,
        transcriber=transcriber,
        ckpt=ckpt,
        job_id=job_id,
    )

    if transcript_result is None or not transcript_result.text.strip():
        raise PodcastTranscriptionError(
            f"could not obtain any transcript for {canonical}"
        )

    # If we still don't have metadata, try to extract title from page
    if not metadata.episode_title and not metadata.show_title:
        try:
            html = fetcher.fetch_page(canonical)
            metadata.episode_title = _extract_title_from_html(html)
        except Exception as exc:
            logger.info(
                "title extraction from %s failed: %s — continuing with empty title",
                canonical, exc,
            )

    # Stage 3: metadata_extracted checkpoint
    if not ckpt.is_complete("metadata_extracted"):
        ckpt.start("metadata_extracted")
        ckpt.complete(
            "metadata_extracted",
            {
                "episode_title": metadata.episode_title,
                "show_title": metadata.show_title,
                "published_at": metadata.published_at,
                "duration_s": int(metadata.duration_s) if metadata.duration_s else None,
            },
        )

    transcript_text = transcript_result.text
    transcript_source = transcript_result.source
    transcript_words = len(transcript_text.split())

    # Stage 4: summarization (for long transcripts)
    cached_summary = ckpt.get_output("summarized")
    if cached_summary is not None:
        summary_result = None if cached_summary.get("skipped") else cached_summary
        summarized = summary_result is not None
    else:
        ckpt.start("summarized")
        title_for_summary = metadata.episode_title or metadata.show_title or ""
        summary_result = summarizer(transcript_text, title_for_summary, canonical)
        summarized = summary_result is not None
        ckpt.complete(
            "summarized",
            summary_result if summary_result is not None else {"skipped": True},
        )

    # Content hash + idempotency (kept alongside stage checkpoints; DB-level
    # dedup still applies when the same content reappears under a new URL).
    content_hash = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()

    existing_hash = conn.execute(
        "SELECT id FROM documents WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if existing_hash is not None:
        existing_id = int(existing_hash["id"])
        chunk_count_row = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
        ).fetchone()
        chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "document_id": existing_id,
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "url": canonical,
            "episode_title": metadata.episode_title,
            "show_title": metadata.show_title,
            "transcript_source": transcript_source,
            "transcript_words": transcript_words,
            "summarized": summarized,
        }

    # Stage 5: write vault file + insert documents row (doc_written).
    ckpt.start("doc_written")
    fetched_at = datetime.now(UTC)
    vault_path = _write_vault_file(
        canonical_url=canonical,
        episode_title=metadata.episode_title,
        show_title=metadata.show_title,
        published_at=metadata.published_at,
        duration_s=metadata.duration_s,
        transcript_source=transcript_source,
        transcript_text=transcript_text,
        transcript_words=transcript_words,
        summarized=summarized,
        summary=summary_result,
        fetched_at=fetched_at,
    )

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, author, content_hash,
                 raw_path, source_id, status)
            VALUES ('podcast', ?, ?, ?, ?, ?, ?, 'ingesting')
            """,
            (
                canonical,
                metadata.episode_title,
                metadata.show_title,
                content_hash,
                str(vault_path),
                canonical,
            ),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]
    ckpt.complete(
        "doc_written",
        {
            "document_id": document_id,
            "vault_path": str(vault_path),
            "content_hash": content_hash,
        },
    )

    # Step 7: Embed. Prepend a short metadata header (show, episode, URL)
    # so title-based semantic search can match chunk 0 without relying on
    # the host happening to say the show or episode name inside the
    # transcript.
    from commonplace_server.pipeline import embed_document

    body_text = transcript_text
    if summarized and summary_result:
        parts = [summary_result.get("description", "")]
        for kp in summary_result.get("key_points", []):
            parts.append(kp)
        for q in summary_result.get("quotes", []):
            parts.append(q)
        body_text = "\n\n".join(parts)

    header = render_embed_header(
        [
            ("Episode", metadata.episode_title),
            ("Show", metadata.show_title),
            ("URL", canonical),
            ("Published", metadata.published_at),
        ]
    )
    embed_text = header + body_text

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    result = embed_document(document_id, embed_text, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested podcast document_id=%d chunks=%d url=%s "
        "transcript_source=%s elapsed_ms=%.0f",
        document_id,
        result.chunk_count,
        canonical,
        transcript_source,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "url": canonical,
        "episode_title": metadata.episode_title,
        "show_title": metadata.show_title,
        "transcript_source": transcript_source,
        "transcript_words": transcript_words,
        "summarized": summarized,
    }


def _obtain_transcript(
    *,
    canonical: str,
    fetcher: Any,
    rss_parser: Any,
    transcriber: Any,
    ckpt: Any,
    job_id: int | None,
) -> tuple[TranscriptResult | None, PodcastMetadata]:
    """Execute the transcript-acquisition stage.

    Checks the ``transcript_obtained`` checkpoint first; if present and the
    text file still exists on disk, re-use it. Otherwise try RSS, then fall
    back to Whisper (downloading audio to ``stage_cache_dir(job_id)`` so a
    crash between download and transcription doesn't force a re-download).

    Tests call the handler without ``_job_id`` — in that case the durable
    cache is unavailable and we fall back to the previous
    ``tempfile.TemporaryDirectory`` behaviour with no checkpointing.
    """
    # Replay path: transcript already obtained on a prior attempt.
    cached = ckpt.get_output("transcript_obtained")
    if cached and isinstance(cached.get("text_path"), str):
        text_path = Path(cached["text_path"])
        if text_path.exists():
            try:
                text = text_path.read_text()
            except OSError:
                text = ""
            if text.strip():
                meta_cached = ckpt.get_output("metadata_extracted") or {}
                metadata = PodcastMetadata(
                    episode_title=meta_cached.get("episode_title"),
                    show_title=meta_cached.get("show_title"),
                    published_at=meta_cached.get("published_at"),
                    duration_s=float(meta_cached.get("duration_s") or 0.0),
                )
                return (
                    TranscriptResult(
                        text=text, source=cached.get("source", "unknown")
                    ),
                    metadata,
                )

    metadata = PodcastMetadata()
    transcript_result: TranscriptResult | None = None
    download_url: str | None = None

    ckpt.start("transcript_obtained")

    # Pocket Casts share links (pca.st / pocketcasts.com) are React SPAs
    # with no server-rendered RSS <link> tag and no extractor in yt-dlp.
    # Resolve them once to a direct enclosure URL via iTunes Search + RSS
    # match, then skip RSS-in-page discovery entirely.
    if _is_pocketcasts_url(canonical):
        resolved = resolve_pocketcasts_url(canonical)
        if resolved:
            download_url = resolved
        else:
            logger.info(
                "pocketcasts resolution failed for %s; Whisper fallback may fail",
                canonical,
            )

    # Try RSS transcript discovery (skipped when we already resolved a
    # Pocket Casts link — the resolver only returns an enclosure URL, not
    # an RSS item with a <podcast:transcript> tag, so there's no transcript
    # to discover and we save one round trip).
    if download_url is None:
        try:
            transcript_result, metadata = _try_rss_transcript(
                canonical, fetcher, rss_parser
            )
        except PodcastFetchError:
            logger.info("RSS discovery failed for %s, will try Whisper", canonical)
        except Exception:
            logger.warning(
                "unexpected error during RSS discovery for %s",
                canonical, exc_info=True,
            )

    # Whisper fallback if no RSS transcript.
    if transcript_result is None:
        logger.info("no RSS transcript for %s, falling back to Whisper", canonical)
        transcript_result = _whisper_fallback(
            canonical=canonical,
            fetcher=fetcher,
            transcriber=transcriber,
            ckpt=ckpt,
            job_id=job_id,
            download_url=download_url,
        )

    if transcript_result is None or not transcript_result.text.strip():
        return None, metadata

    # Persist transcript text to durable scratch so a crash between now and
    # the vault write doesn't force us to re-transcribe on retry.
    if job_id is not None:
        cache_dir = stage_cache_dir(job_id)
        text_path = cache_dir / "transcript.txt"
        text_path.write_text(transcript_result.text)
        ckpt.complete(
            "transcript_obtained",
            {"text_path": str(text_path), "source": transcript_result.source},
        )

    return transcript_result, metadata


def _whisper_fallback(
    *,
    canonical: str,
    fetcher: Any,
    transcriber: Any,
    ckpt: Any,
    job_id: int | None,
    download_url: str | None = None,
) -> TranscriptResult | None:
    """Download audio and run Whisper; checkpoint the downloaded WAV when possible.

    ``download_url`` overrides the URL passed to the audio downloader when
    the canonical URL points at a landing page (e.g. pca.st / pocketcasts)
    whose audio can only be reached after resolving to an RSS enclosure.
    When ``None`` (the normal case) the canonical URL itself is used.

    When ``job_id`` is available the wav file lives under
    :func:`stage_cache_dir` so it survives a crash — a retry reads back the
    ``audio_downloaded`` checkpoint and skips the (often multi-minute)
    yt-dlp call. Without a job_id (direct test calls) we fall back to the
    pre-feature ``tempfile.TemporaryDirectory`` behaviour.

    Retry semantics: transient failures (yt-dlp network errors, Whisper
    crashes) raise :class:`RetryableHandlerError` **only** when running
    under the worker (``job_id is not None``). In direct-call mode we
    preserve the pre-feature behaviour and re-raise the native
    :class:`PodcastFetchError` / :class:`PodcastTranscriptionError` so
    tests that call the handler synchronously still see typed exceptions.
    """
    fetch_url = download_url or canonical
    try:
        if job_id is not None:
            cache_dir = stage_cache_dir(job_id)
            cached = ckpt.get_output("audio_downloaded")
            audio_path: Path | None = None
            if cached and isinstance(cached.get("wav_path"), str):
                maybe = Path(cached["wav_path"])
                if maybe.exists():
                    audio_path = maybe
            if audio_path is None:
                ckpt.start("audio_downloaded")
                audio_base = cache_dir / "audio"
                audio_path = fetcher.download_audio(fetch_url, audio_base)
                ckpt.complete(
                    "audio_downloaded", {"wav_path": str(audio_path)}
                )
            return transcriber(audio_path)  # type: ignore[no-any-return]
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_base = Path(tmpdir) / "audio"
                tmp_audio: Path = fetcher.download_audio(fetch_url, audio_base)
                try:
                    return transcriber(tmp_audio)  # type: ignore[no-any-return]
                finally:
                    if tmp_audio.exists():
                        tmp_audio.unlink()
    except PodcastFetchError as exc:
        if job_id is not None:
            raise RetryableHandlerError(
                f"podcast audio download failed for {canonical}: {exc}"
            ) from exc
        raise
    except Exception as exc:
        # Whisper / transcriber crash.
        if job_id is not None:
            raise RetryableHandlerError(
                f"Whisper fallback failed for {canonical}: {exc}"
            ) from exc
        raise PodcastTranscriptionError(
            f"Whisper fallback failed for {canonical}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# RSS transcript discovery pipeline
# ---------------------------------------------------------------------------


def _try_rss_transcript(
    episode_url: str,
    fetcher: Any,
    rss_parser: Any,
) -> tuple[TranscriptResult | None, PodcastMetadata]:
    """Attempt to find a transcript via RSS feed discovery.

    Returns (TranscriptResult | None, PodcastMetadata).
    If no transcript found, TranscriptResult is None but metadata may be populated.
    """
    metadata = PodcastMetadata()
    feed_url: str | None = None

    # Strategy 1: Fetch the page, look for RSS link in HTML
    try:
        html = fetcher.fetch_page(episode_url)
        feed_url = _find_rss_link_in_html(html)
        if not metadata.episode_title:
            metadata.episode_title = _extract_title_from_html(html)
    except PodcastFetchError:
        pass

    # Strategy 2: Apple Podcasts → iTunes lookup API
    if feed_url is None:
        apple_id = _apple_podcast_id(episode_url)
        if apple_id:
            feed_url = fetcher.fetch_apple_feed_url(apple_id)

    if feed_url is None:
        return None, metadata

    # Parse the RSS feed
    feed = rss_parser.parse_feed(feed_url)
    if not feed or not getattr(feed, "entries", None):
        return None, metadata

    # Extract show-level metadata
    if hasattr(feed, "feed"):
        metadata.show_title = getattr(feed.feed, "title", None)

    # Match the episode
    entry = _match_episode_in_feed(feed, episode_url)
    if entry is None:
        # No matching episode found — can't get transcript from RSS
        return None, metadata

    # Update metadata from matched entry
    entry_meta = _extract_metadata_from_entry(entry, feed)
    metadata.episode_title = entry_meta.episode_title or metadata.episode_title
    metadata.show_title = entry_meta.show_title or metadata.show_title
    metadata.published_at = entry_meta.published_at
    metadata.duration_s = entry_meta.duration_s
    metadata.description = entry_meta.description

    # Look for transcript tag
    transcript_info = _find_transcript_url_in_entry(entry)
    if transcript_info is None:
        return None, metadata

    transcript_url, transcript_type = transcript_info

    # Fetch the transcript
    try:
        raw_text, content_type = fetcher.fetch_transcript(transcript_url)
    except PodcastFetchError:
        logger.warning("failed to fetch RSS transcript at %s", transcript_url)
        return None, metadata

    if not raw_text or not raw_text.strip():
        return None, metadata

    cleaned = _clean_transcript(raw_text, content_type or transcript_type)
    if not cleaned.strip():
        return None, metadata

    return TranscriptResult(text=cleaned, source="rss_transcript"), metadata
