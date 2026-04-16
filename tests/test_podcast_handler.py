"""Tests for commonplace_worker/handlers/podcast.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from commonplace_db.db import migrate
from commonplace_worker.handlers.podcast import (
    PodcastFetchError,
    PodcastTranscriptionError,
    TranscriptResult,
    _canonicalize_url,
    _clean_srt,
    _clean_transcript,
    _clean_vtt,
    _parse_duration,
    handle_podcast_ingest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite with sqlite-vec + migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(root))
    return root


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[0.0] * 768 for _ in texts]


# Good transcript text with punctuation and reasonable content
_GOOD_TRANSCRIPT = (
    "Welcome to this podcast about software design. "
    "Today we'll discuss the importance of clean architecture. "
    "First, let's talk about separation of concerns. "
    "Each module should have a single responsibility. "
    "This makes the code easier to test and maintain. "
    "Next, we'll look at dependency injection. "
    "By injecting dependencies, we can swap implementations easily. "
    "This is especially useful for testing. "
    "Finally, let's consider error handling. "
    "Good error handling makes software more robust. "
) * 5

# Long transcript for summarization (>2000 words)
_LONG_TRANSCRIPT = _GOOD_TRANSCRIPT * 10

_SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Test Show</title>
    <item>
      <title>Episode 42: Software Design</title>
      <link>https://podcast.example.com/episodes/42</link>
      <itunes:duration>01:23:45</itunes:duration>
      <pubDate>Mon, 15 Apr 2024 12:00:00 GMT</pubDate>
      <description>A great episode about design.</description>
      <podcast:transcript url="https://cdn.example.com/transcript-42.txt" type="text/plain" />
    </item>
    <item>
      <title>Episode 41: Testing</title>
      <link>https://podcast.example.com/episodes/41</link>
      <itunes:duration>45:30</itunes:duration>
    </item>
  </channel>
</rss>"""

_SAMPLE_HTML_WITH_RSS = """<!DOCTYPE html>
<html>
<head>
  <title>Episode 42: Software Design - Test Show</title>
  <link rel="alternate" type="application/rss+xml" href="https://podcast.example.com/feed.xml" />
</head>
<body><p>Episode page</p></body>
</html>"""

_SAMPLE_HTML_NO_RSS = """<!DOCTYPE html>
<html>
<head><title>Some Podcast Episode</title></head>
<body><p>No RSS link here</p></body>
</html>"""


# ---------------------------------------------------------------------------
# Mock fetcher
# ---------------------------------------------------------------------------


class FakeFetcher:
    """Mock fetcher for HTTP operations."""

    def __init__(
        self,
        *,
        page_html: str = _SAMPLE_HTML_WITH_RSS,
        transcript_text: str = _GOOD_TRANSCRIPT,
        transcript_content_type: str = "text/plain",
        apple_feed_url: str | None = None,
        fail_page: bool = False,
        fail_transcript: bool = False,
        fail_audio: bool = False,
    ):
        self._page_html = page_html
        self._transcript_text = transcript_text
        self._transcript_content_type = transcript_content_type
        self._apple_feed_url = apple_feed_url
        self._fail_page = fail_page
        self._fail_transcript = fail_transcript
        self._fail_audio = fail_audio

    def fetch_page(self, url: str) -> str:
        if self._fail_page:
            raise PodcastFetchError(f"failed to fetch {url}")
        return self._page_html

    def fetch_transcript(self, url: str) -> tuple[str, str]:
        if self._fail_transcript:
            raise PodcastFetchError(f"failed to fetch transcript at {url}")
        return self._transcript_text, self._transcript_content_type

    def fetch_apple_feed_url(self, podcast_id: str) -> str | None:
        return self._apple_feed_url

    def download_audio(self, url: str, output_path: Path) -> Path:
        if self._fail_audio:
            raise PodcastFetchError("yt-dlp audio download failed")
        # Create a dummy wav file
        wav = output_path.with_suffix(".wav")
        wav.write_bytes(b"RIFF" + b"\x00" * 100)
        return wav


# ---------------------------------------------------------------------------
# Mock RSS parser using feedparser-like structure
# ---------------------------------------------------------------------------


class FakeEntry:
    """Mimics a feedparser entry."""

    def __init__(
        self,
        *,
        title: str = "Episode 42: Software Design",
        link: str = "https://podcast.example.com/episodes/42",
        published: str = "Mon, 15 Apr 2024 12:00:00 GMT",
        itunes_duration: str = "01:23:45",
        summary: str = "A great episode about design.",
        podcast_transcript: Any = None,
        links: list[dict[str, str]] | None = None,
    ):
        self.title = title
        self.link = link
        self.published = published
        self.itunes_duration = itunes_duration
        self.summary = summary
        self.podcast_transcript = podcast_transcript
        self.links = links or []
        self.id = link

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def __iter__(self):
        return iter(
            ["title", "link", "published", "itunes_duration", "summary",
             "podcast_transcript", "links", "id"]
        )


class FakeFeed:
    """Mimics a feedparser feed object."""

    def __init__(self, title: str = "Test Show"):
        self.title = title


class FakeRSSResult:
    """Mimics feedparser.parse() result."""

    def __init__(
        self,
        *,
        entries: list[FakeEntry] | None = None,
        feed_title: str = "Test Show",
    ):
        self.entries = entries or []
        self.feed = FakeFeed(title=feed_title)


class FakeRSSParser:
    """Mock RSS parser."""

    def __init__(
        self,
        *,
        result: FakeRSSResult | None = None,
        fail: bool = False,
    ):
        self._result = result
        self._fail = fail

    def parse_feed(self, feed_url: str) -> Any:
        if self._fail:
            return FakeRSSResult(entries=[])
        return self._result or FakeRSSResult(entries=[])


def _make_rss_with_transcript() -> FakeRSSParser:
    """Create a fake RSS parser with a matching episode that has a transcript."""
    entry = FakeEntry(
        podcast_transcript=[
            {"url": "https://cdn.example.com/transcript-42.txt", "type": "text/plain"}
        ],
    )
    return FakeRSSParser(result=FakeRSSResult(entries=[entry]))


def _make_rss_no_transcript() -> FakeRSSParser:
    """Create a fake RSS parser with a matching episode but no transcript tag."""
    entry = FakeEntry(podcast_transcript=None)
    return FakeRSSParser(result=FakeRSSResult(entries=[entry]))


def _make_rss_no_matching_episode() -> FakeRSSParser:
    """Create a fake RSS parser with entries that don't match the URL."""
    entry = FakeEntry(
        title="Different Episode",
        link="https://podcast.example.com/episodes/99",
        podcast_transcript=[
            {"url": "https://cdn.example.com/transcript-99.txt", "type": "text/plain"}
        ],
    )
    return FakeRSSParser(result=FakeRSSResult(entries=[entry]))


# ---------------------------------------------------------------------------
# Mock transcriber
# ---------------------------------------------------------------------------


def _fake_transcriber(audio_path: Path) -> TranscriptResult:
    """Mock Whisper transcriber."""
    return TranscriptResult(text=_GOOD_TRANSCRIPT, source="whisper")


def _fake_transcriber_fail(audio_path: Path) -> TranscriptResult:
    """Mock Whisper transcriber that fails."""
    raise RuntimeError("Whisper model failed")


# ---------------------------------------------------------------------------
# Mock summarizer
# ---------------------------------------------------------------------------


def _fake_summarizer_returns(
    text: str, title: str, url: str
) -> dict[str, Any] | None:
    return {
        "description": "This is a summary of the podcast episode.",
        "key_points": ["Point 1", "Point 2", "Point 3"],
        "quotes": ["Welcome to this podcast"],
    }


def _fake_summarizer_skip(
    text: str, title: str, url: str
) -> dict[str, Any] | None:
    return None


# ---------------------------------------------------------------------------
# URL normalization tests
# ---------------------------------------------------------------------------


class TestURLNormalization:
    def test_strips_tracking_params(self):
        url = _canonicalize_url(
            "https://podcast.example.com/ep/42?utm_source=twitter&utm_medium=social"
        )
        assert "utm_source" not in url
        assert "utm_medium" not in url
        assert url == "https://podcast.example.com/ep/42"

    def test_preserves_meaningful_params(self):
        url = _canonicalize_url(
            "https://podcast.example.com/ep/42?id=123&utm_source=twitter"
        )
        assert "id=123" in url
        assert "utm_source" not in url

    def test_drops_fragment(self):
        url = _canonicalize_url("https://podcast.example.com/ep/42#timestamp")
        assert "#" not in url

    def test_invalid_scheme_raises(self):
        with pytest.raises(PodcastFetchError, match="unsupported URL scheme"):
            _canonicalize_url("ftp://podcast.example.com/ep/42")

    def test_missing_hostname_raises(self):
        with pytest.raises(PodcastFetchError, match="URL missing hostname"):
            _canonicalize_url("https://")


# ---------------------------------------------------------------------------
# Transcript cleaning tests
# ---------------------------------------------------------------------------


class TestTranscriptCleaning:
    def test_clean_vtt(self):
        vtt = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:04.000
Hello world.

00:00:04.000 --> 00:00:08.000
This is a test.
"""
        cleaned = _clean_vtt(vtt)
        assert "WEBVTT" not in cleaned
        assert "-->" not in cleaned
        assert "Hello world." in cleaned
        assert "This is a test." in cleaned

    def test_clean_srt(self):
        srt = """1
00:00:01,000 --> 00:00:04,000
Hello from SRT.

2
00:00:04,000 --> 00:00:08,000
Another line of text.
"""
        cleaned = _clean_srt(srt)
        assert "Hello from SRT." in cleaned
        assert "Another line of text." in cleaned
        assert "-->" not in cleaned

    def test_clean_transcript_detects_vtt(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello VTT."
        cleaned = _clean_transcript(vtt)
        assert "Hello VTT." in cleaned
        assert "WEBVTT" not in cleaned

    def test_clean_transcript_detects_srt(self):
        srt = "1\n00:00:01,000 --> 00:00:04,000\nHello SRT."
        cleaned = _clean_transcript(srt)
        assert "Hello SRT." in cleaned

    def test_clean_transcript_plain_text(self):
        text = "  This is   plain text   with   spaces.  "
        cleaned = _clean_transcript(text)
        assert cleaned == "This is plain text with spaces."


# ---------------------------------------------------------------------------
# Duration parsing tests
# ---------------------------------------------------------------------------


class TestDurationParsing:
    def test_hhmmss(self):
        assert _parse_duration("01:23:45") == 5025.0

    def test_mmss(self):
        assert _parse_duration("45:30") == 2730.0

    def test_seconds(self):
        assert _parse_duration("3600") == 3600.0

    def test_empty(self):
        assert _parse_duration("") == 0.0


# ---------------------------------------------------------------------------
# Happy path: RSS transcript found
# ---------------------------------------------------------------------------


class TestHandlePodcastIngest:
    def test_happy_path_rss_transcript(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """RSS transcript found and fetched successfully."""
        fetcher = FakeFetcher()
        rss_parser = _make_rss_with_transcript()

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["document_id"] is not None
        assert result["url"] == "https://podcast.example.com/episodes/42"
        assert result["transcript_source"] == "rss_transcript"
        assert result["transcript_words"] > 0
        assert result["summarized"] is False
        assert result["episode_title"] == "Episode 42: Software Design"
        assert result["show_title"] == "Test Show"

        # Verify vault file
        md_files = list(vault_dir.rglob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "source: podcast" in content
        assert "transcript_source: rss_transcript" in content

    def test_whisper_fallback_no_rss(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """No RSS feed found → Whisper fallback."""
        fetcher = FakeFetcher(page_html=_SAMPLE_HTML_NO_RSS)
        rss_parser = FakeRSSParser(result=FakeRSSResult(entries=[]))

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _transcriber=_fake_transcriber,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["transcript_source"] == "whisper"
        assert result["document_id"] is not None

    def test_whisper_fallback_episode_not_matched(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """RSS feed found but episode not matched → Whisper fallback."""
        fetcher = FakeFetcher()
        rss_parser = _make_rss_no_matching_episode()

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _transcriber=_fake_transcriber,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["transcript_source"] == "whisper"

    def test_whisper_fallback_no_transcript_tag(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """RSS feed found, episode matched, but no podcast:transcript tag → Whisper."""
        fetcher = FakeFetcher()
        rss_parser = _make_rss_no_transcript()

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _transcriber=_fake_transcriber,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["transcript_source"] == "whisper"

    def test_idempotency(self, db_conn: sqlite3.Connection, vault_dir: Path):
        """Same URL ingested twice returns same document_id."""
        fetcher = FakeFetcher()
        rss_parser = _make_rss_with_transcript()
        payload = {"url": "https://podcast.example.com/episodes/42"}

        r1 = handle_podcast_ingest(
            payload, db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        r2 = handle_podcast_ingest(
            payload, db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        assert r1["document_id"] == r2["document_id"]

    def test_transcript_from_srt_format(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """SRT transcript from RSS is cleaned to plain text."""
        srt_text = (
            "1\n00:00:01,000 --> 00:00:04,000\nHello from the podcast.\n\n"
            "2\n00:00:04,000 --> 00:00:08,000\nThis is episode forty two.\n"
        )
        fetcher = FakeFetcher(
            transcript_text=srt_text,
            transcript_content_type="application/x-subrip",
        )
        rss_parser = _make_rss_with_transcript()

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["transcript_source"] == "rss_transcript"
        # Check the vault file has cleaned text
        md_files = list(vault_dir.rglob("*.md"))
        content = md_files[0].read_text()
        assert "Hello from the podcast." in content
        assert "-->" not in content

    def test_transcript_from_vtt_format(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """VTT transcript from RSS is cleaned to plain text."""
        vtt_text = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:04.000\nHello from VTT podcast.\n\n"
            "00:00:04.000 --> 00:00:08.000\nThis is VTT format.\n"
        )
        fetcher = FakeFetcher(
            transcript_text=vtt_text,
            transcript_content_type="text/vtt",
        )
        rss_parser = _make_rss_with_transcript()

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["transcript_source"] == "rss_transcript"
        md_files = list(vault_dir.rglob("*.md"))
        content = md_files[0].read_text()
        assert "Hello from VTT podcast." in content
        assert "WEBVTT" not in content

    def test_invalid_url_rejected(self):
        """Non-HTTP URL raises PodcastFetchError."""
        conn = sqlite3.connect(":memory:")
        with pytest.raises((ValueError, PodcastFetchError)):
            handle_podcast_ingest(
                {"url": "ftp://podcast.example.com/ep/42"},
                conn,
            )

    def test_missing_url_raises(self):
        """Missing URL in payload raises ValueError."""
        conn = sqlite3.connect(":memory:")
        with pytest.raises(ValueError, match="missing 'url'"):
            handle_podcast_ingest({"url": ""}, conn)

    def test_network_failure_typed_exception(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Network failure during page fetch + audio download failure → typed exception."""
        fetcher = FakeFetcher(fail_page=True, fail_audio=True)
        rss_parser = FakeRSSParser(fail=True)

        with pytest.raises((PodcastFetchError, PodcastTranscriptionError)):
            handle_podcast_ingest(
                {"url": "https://podcast.example.com/episodes/42"},
                db_conn,
                _fetcher=fetcher,
                _rss_parser=rss_parser,
                _transcriber=_fake_transcriber_fail,
                _summarizer=_fake_summarizer_skip,
                _embedder=_fake_embedder,
            )

    def test_summary_invoked_for_long_transcript(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Summarizer is called and result used for long transcripts."""
        fetcher = FakeFetcher(transcript_text=_LONG_TRANSCRIPT)
        rss_parser = _make_rss_with_transcript()

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_returns,
            _embedder=_fake_embedder,
        )

        assert result["summarized"] is True
        md_files = list(vault_dir.rglob("*.md"))
        content = md_files[0].read_text()
        assert "## Summary" in content
        assert "## Key points" in content
        assert "summarized: true" in content

    def test_summary_skipped_for_short_transcript(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Short transcripts are not summarized."""
        fetcher = FakeFetcher(transcript_text="Short episode content here.")
        rss_parser = _make_rss_with_transcript()

        result = handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["summarized"] is False

    def test_vault_file_has_correct_frontmatter(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Vault file contains all expected frontmatter fields."""
        fetcher = FakeFetcher()
        rss_parser = _make_rss_with_transcript()

        handle_podcast_ingest(
            {"url": "https://podcast.example.com/episodes/42"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        md_files = list(vault_dir.rglob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "source: podcast" in content
        assert "url:" in content
        assert "episode_title:" in content
        assert "show_title:" in content
        assert "transcript_source:" in content
        assert "transcript_words:" in content
        assert "summarized:" in content
        assert "fetched_at:" in content

    def test_tracking_params_stripped_in_canonical_url(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """URLs with tracking params are normalized before storage."""
        fetcher = FakeFetcher()
        rss_parser = _make_rss_with_transcript()

        result = handle_podcast_ingest(
            {
                "url": "https://podcast.example.com/episodes/42?utm_source=twitter&utm_medium=social"
            },
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert "utm_source" not in result["url"]
        assert result["url"] == "https://podcast.example.com/episodes/42"

    def test_apple_podcasts_feed_discovery(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Apple Podcasts URL triggers iTunes lookup for feed URL."""
        entry = FakeEntry(
            title="Apple Episode",
            link="https://podcasts.apple.com/us/podcast/episode/id1234567890?i=1000",
        )
        entry.podcast_transcript = [
            {"url": "https://cdn.example.com/transcript.txt", "type": "text/plain"}
        ]

        fetcher = FakeFetcher(
            page_html=_SAMPLE_HTML_NO_RSS,
            apple_feed_url="https://feeds.example.com/podcast.xml",
        )
        rss_parser = FakeRSSParser(
            result=FakeRSSResult(
                entries=[entry],
                feed_title="Apple Show",
            )
        )

        result = handle_podcast_ingest(
            {"url": "https://podcasts.apple.com/us/podcast/episode/id1234567890?i=1000"},
            db_conn,
            _fetcher=fetcher,
            _rss_parser=rss_parser,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )

        assert result["document_id"] is not None
        assert result["transcript_source"] == "rss_transcript"
