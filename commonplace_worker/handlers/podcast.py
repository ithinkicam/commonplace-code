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
import os
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


def _slugify(text: str, max_len: int = 60) -> str:
    """Turn ``text`` into a URL-safe slug (lowercase, hyphen-separated)."""
    lowered = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if not slug:
        slug = "podcast"
    return slug[:max_len].rstrip("-") or "podcast"


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
        except Exception:
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
    """Invoke the summarize_capture skill via claude CLI.

    Returns parsed summary dict or None if summarization is not needed
    or fails.
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
        result = subprocess.run(  # noqa: S603
            [
                "claude", "-p",
                "--system-prompt-file", str(skill_path),
                "--model", "haiku",
                input_json,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("summarize_capture skill invocation failed")
        return None

    if result.returncode != 0:
        logger.warning(
            "summarize_capture exited %d: %s", result.returncode, result.stderr[:200]
        )
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


def _extract_title_from_html(html: str) -> str | None:
    """Best-effort title extraction from HTML <title> tag."""
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Audio download via yt-dlp
# ---------------------------------------------------------------------------


def _download_audio(url: str, output_path: Path) -> Path:
    """Download podcast audio via yt-dlp for Whisper transcription."""
    try:
        subprocess.run(  # noqa: S603
            [
                "yt-dlp",
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

    wav_path = output_path.with_suffix(".wav")
    if not wav_path.exists():
        raise PodcastFetchError(
            f"yt-dlp did not produce audio file at {wav_path}"
        )
    return wav_path


# ---------------------------------------------------------------------------
# Vault writing
# ---------------------------------------------------------------------------


def _vault_root() -> Path:
    root = os.environ.get("COMMONPLACE_VAULT_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / "commonplace"


def _yaml_escape(value: str) -> str:
    """Minimal YAML-safe escaping for a single-line scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
    vault_root = _vault_root()
    year = fetched_at.strftime("%Y")
    month = fetched_at.strftime("%m")
    out_dir = vault_root / "captures" / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = fetched_at.strftime("%Y-%m-%dT%H%M%SZ")
    slug_src = episode_title or show_title or "episode"
    slug = _slugify(slug_src)
    filename = f"{ts}-podcast-{slug}.md"
    final_path = out_dir / filename
    tmp_path = out_dir / f"{filename}.tmp"

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

    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(final_path)
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
    lines.append(f"url: {_yaml_escape(canonical_url)}")
    if episode_title:
        lines.append(f"episode_title: {_yaml_escape(episode_title)}")
    if show_title:
        lines.append(f"show_title: {_yaml_escape(show_title)}")
    if published_at:
        lines.append(f"published_at: {_yaml_escape(published_at)}")
    lines.append(f"duration_s: {duration_s:.0f}")
    lines.append(f"transcript_source: {transcript_source}")
    lines.append(f"transcript_words: {transcript_words}")
    lines.append(f"summarized: {'true' if summarized else 'false'}")
    lines.append(
        f"fetched_at: {_yaml_escape(fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
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

    canonical = _canonicalize_url(url_raw.strip())

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

    # Step 1: Try to discover RSS feed and get transcript
    metadata = PodcastMetadata()
    transcript_result: TranscriptResult | None = None

    try:
        transcript_result, metadata = _try_rss_transcript(
            canonical, fetcher, rss_parser
        )
    except PodcastFetchError:
        logger.info("RSS discovery failed for %s, will try Whisper", canonical)
    except Exception:
        logger.warning(
            "unexpected error during RSS discovery for %s", canonical, exc_info=True
        )

    # Step 2: Whisper fallback if no RSS transcript
    if transcript_result is None:
        logger.info("no RSS transcript for %s, falling back to Whisper", canonical)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_base = Path(tmpdir) / "audio"
                audio_path = fetcher.download_audio(canonical, audio_base)
                try:
                    transcript_result = transcriber(audio_path)
                finally:
                    if audio_path.exists():
                        audio_path.unlink()
        except PodcastFetchError:
            raise
        except Exception as exc:
            raise PodcastTranscriptionError(
                f"Whisper fallback failed for {canonical}: {exc}"
            ) from exc

    if transcript_result is None or not transcript_result.text.strip():
        raise PodcastTranscriptionError(
            f"could not obtain any transcript for {canonical}"
        )

    # If we still don't have metadata, try to extract title from page
    if not metadata.episode_title and not metadata.show_title:
        try:
            html = fetcher.fetch_page(canonical)
            metadata.episode_title = _extract_title_from_html(html)
        except Exception:
            pass  # Best effort

    transcript_text = transcript_result.text
    transcript_source = transcript_result.source
    transcript_words = len(transcript_text.split())

    # Step 3: Summarization (for long transcripts)
    title_for_summary = metadata.episode_title or metadata.show_title or ""
    summary_result = summarizer(transcript_text, title_for_summary, canonical)
    summarized = summary_result is not None

    # Step 4: Content hash + idempotency
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

    # Step 5: Write vault file
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

    # Step 6: Insert documents row
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

    # Step 7: Embed
    from commonplace_server.pipeline import embed_document

    embed_text = transcript_text
    if summarized and summary_result:
        parts = [summary_result.get("description", "")]
        for kp in summary_result.get("key_points", []):
            parts.append(kp)
        for q in summary_result.get("quotes", []):
            parts.append(q)
        embed_text = "\n\n".join(parts)

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
