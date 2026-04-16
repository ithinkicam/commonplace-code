"""Tests for commonplace_worker/handlers/youtube.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from commonplace_db.db import migrate
from commonplace_worker.handlers.youtube import (
    CaptionResult,
    YouTubeFetchError,
    YouTubeTranscriptionError,
    _canonical_url,
    _caption_quality_ok,
    _clean_vtt,
    _extract_video_id,
    handle_youtube_ingest,
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
    "Welcome to this video about software design. "
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


class FakeDownloader:
    """Mock downloader for yt-dlp operations."""

    def __init__(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        manual_captions: str | None = None,
        auto_captions: str | None = None,
        has_manual: bool = True,
        audio_path: Path | None = None,
        fail_metadata: bool = False,
        fail_captions: bool = False,
        fail_audio: bool = False,
    ):
        self._metadata = metadata or {
            "title": "Test Video",
            "channel": "Test Channel",
            "upload_date": "20240315",
            "duration": 600,
            "subtitles": {"en": [{"ext": "vtt"}]} if has_manual else {},
            "automatic_captions": {"en": [{"ext": "vtt"}]},
        }
        self._manual_captions = manual_captions
        self._auto_captions = auto_captions
        self._audio_path = audio_path
        self._fail_metadata = fail_metadata
        self._fail_captions = fail_captions
        self._fail_audio = fail_audio

    def get_metadata(self, url: str) -> dict[str, Any]:
        if self._fail_metadata:
            raise YouTubeFetchError(f"yt-dlp failed for {url}")
        return self._metadata

    def get_captions(self, url: str, lang: str = "en") -> tuple[str | None, str | None]:
        if self._fail_captions:
            raise YouTubeFetchError(f"yt-dlp captions failed for {url}")
        return self._manual_captions, self._auto_captions

    def download_audio(self, url: str, output_path: Path) -> Path:
        if self._fail_audio:
            raise YouTubeFetchError("yt-dlp audio download failed")
        if self._audio_path:
            return self._audio_path
        # Create a dummy wav file
        wav = output_path.with_suffix(".wav")
        wav.write_bytes(b"RIFF" + b"\x00" * 100)
        return wav


def _fake_transcriber(audio_path: Path) -> CaptionResult:
    """Mock Whisper transcriber."""
    return CaptionResult(text=_GOOD_TRANSCRIPT, source="whisper")


def _fake_transcriber_fail(audio_path: Path) -> CaptionResult:
    """Mock Whisper transcriber that fails."""
    raise RuntimeError("Whisper model failed")


def _fake_summarizer_returns(text: str, title: str, url: str) -> dict[str, Any] | None:
    """Mock summarizer that always returns a summary."""
    return {
        "description": "This is a summary of the video.",
        "key_points": ["Point 1", "Point 2", "Point 3"],
        "quotes": ["Welcome to this video"],
    }


def _fake_summarizer_skip(text: str, title: str, url: str) -> dict[str, Any] | None:
    """Mock summarizer that skips (content too short)."""
    return None


# ---------------------------------------------------------------------------
# URL normalization tests
# ---------------------------------------------------------------------------


class TestURLNormalization:
    def test_standard_watch_url(self):
        vid = _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert vid == "dQw4w9WgXcQ"
        assert _canonical_url(vid) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_youtu_be_short_link(self):
        vid = _extract_video_id("https://youtu.be/dQw4w9WgXcQ")
        assert vid == "dQw4w9WgXcQ"
        assert _canonical_url(vid) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_shorts_url(self):
        vid = _extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert vid == "dQw4w9WgXcQ"

    def test_playlist_params_stripped(self):
        vid = _extract_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        )
        assert vid == "dQw4w9WgXcQ"
        assert _canonical_url(vid) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_embed_url(self):
        vid = _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ")
        assert vid == "dQw4w9WgXcQ"

    def test_http_without_www(self):
        vid = _extract_video_id("http://youtube.com/watch?v=dQw4w9WgXcQ")
        assert vid == "dQw4w9WgXcQ"

    def test_invalid_url_raises(self):
        with pytest.raises(YouTubeFetchError, match="not a recognized YouTube URL"):
            _extract_video_id("https://example.com/not-youtube")

    def test_empty_url_raises(self):
        with pytest.raises(YouTubeFetchError):
            _extract_video_id("")

    def test_non_youtube_domain_raises(self):
        with pytest.raises(YouTubeFetchError):
            _extract_video_id("https://vimeo.com/12345")


# ---------------------------------------------------------------------------
# Caption quality tests
# ---------------------------------------------------------------------------


class TestCaptionQuality:
    def test_good_captions_pass(self):
        assert _caption_quality_ok(_GOOD_TRANSCRIPT, 600.0) is True

    def test_empty_text_fails(self):
        assert _caption_quality_ok("", 600.0) is False

    def test_short_text_fails(self):
        assert _caption_quality_ok("hello", 600.0) is False

    def test_no_punctuation_fails(self):
        text = " ".join(["word"] * 500)
        assert _caption_quality_ok(text, 600.0) is False

    def test_excessive_repetition_fails(self):
        text = "the same phrase. " * 100
        assert _caption_quality_ok(text, 600.0) is False


# ---------------------------------------------------------------------------
# VTT cleaning tests
# ---------------------------------------------------------------------------


class TestCleanVTT:
    def test_strips_headers_and_timestamps(self):
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

    def test_deduplicates_consecutive_lines(self):
        vtt = """WEBVTT

00:00:01.000 --> 00:00:04.000
Hello world.

00:00:04.000 --> 00:00:08.000
Hello world.

00:00:08.000 --> 00:00:12.000
Different text.
"""
        cleaned = _clean_vtt(vtt)
        # "Hello world." should appear once (deduped)
        assert cleaned.count("Hello world.") == 1


# ---------------------------------------------------------------------------
# Happy path: manual captions
# ---------------------------------------------------------------------------


class TestHandleYoutubeIngest:
    def test_happy_path_manual_captions(self, db_conn: sqlite3.Connection, vault_dir: Path):
        downloader = FakeDownloader(
            manual_captions=_GOOD_TRANSCRIPT,
            has_manual=True,
        )
        result = handle_youtube_ingest(
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        assert result["document_id"] is not None
        assert result["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert result["title"] == "Test Video"
        assert result["caption_source"] == "manual"
        assert result["transcript_words"] > 0
        assert result["summarized"] is False
        assert result["chunk_count"] >= 0

        # Verify vault file was written
        md_files = list(vault_dir.rglob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "source: youtube" in content
        assert "dQw4w9WgXcQ" in content

    def test_auto_caption_fallback(self, db_conn: sqlite3.Connection, vault_dir: Path):
        """When manual captions unavailable, use auto captions."""
        downloader = FakeDownloader(
            manual_captions=None,
            auto_captions=_GOOD_TRANSCRIPT,
            has_manual=False,
        )
        result = handle_youtube_ingest(
            {"url": "https://youtu.be/dQw4w9WgXcQ"},
            db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        assert result["caption_source"] == "auto"
        assert result["document_id"] is not None

    def test_whisper_fallback(self, db_conn: sqlite3.Connection, vault_dir: Path):
        """When captions fail quality check, fall back to Whisper."""
        downloader = FakeDownloader(
            manual_captions=None,
            auto_captions=None,
            has_manual=False,
        )
        result = handle_youtube_ingest(
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            db_conn,
            _downloader=downloader,
            _transcriber=_fake_transcriber,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        assert result["caption_source"] == "whisper"
        assert result["document_id"] is not None

    def test_idempotency(self, db_conn: sqlite3.Connection, vault_dir: Path):
        """Same URL ingested twice returns same document_id, no re-embed."""
        downloader = FakeDownloader(manual_captions=_GOOD_TRANSCRIPT, has_manual=True)
        payload = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}

        r1 = handle_youtube_ingest(
            payload, db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        r2 = handle_youtube_ingest(
            payload, db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        assert r1["document_id"] == r2["document_id"]

    def test_summary_invoked_for_long_transcript(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Summarizer is called and result used for long transcripts."""
        downloader = FakeDownloader(
            manual_captions=_LONG_TRANSCRIPT,
            has_manual=True,
        )
        result = handle_youtube_ingest(
            {"url": "https://www.youtube.com/watch?v=abcdefghijk"},
            db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_returns,
            _embedder=_fake_embedder,
        )
        assert result["summarized"] is True
        assert result["document_id"] is not None

        # Verify vault file contains summary
        md_files = list(vault_dir.rglob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "## Summary" in content
        assert "## Key points" in content
        assert "summarized: true" in content

    def test_summary_skipped_for_short_transcript(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Short transcripts are not summarized."""
        short_text = "This is a short video. Just a few words."
        downloader = FakeDownloader(
            manual_captions=short_text,
            has_manual=True,
        )
        result = handle_youtube_ingest(
            {"url": "https://www.youtube.com/watch?v=shortvideoID"},
            db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        assert result["summarized"] is False

    def test_invalid_url_rejected(self):
        """Non-YouTube URLs raise ValueError or YouTubeFetchError."""
        conn = sqlite3.connect(":memory:")
        with pytest.raises((ValueError, YouTubeFetchError)):
            handle_youtube_ingest(
                {"url": "https://example.com/not-youtube"},
                conn,
            )

    def test_missing_url_raises(self):
        """Missing URL in payload raises ValueError."""
        conn = sqlite3.connect(":memory:")
        with pytest.raises(ValueError, match="missing 'url'"):
            handle_youtube_ingest({"url": ""}, conn)

    def test_ytdlp_failure_raises_typed_exception(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """yt-dlp metadata failure surfaces as YouTubeFetchError."""
        downloader = FakeDownloader(fail_metadata=True)
        with pytest.raises(YouTubeFetchError):
            handle_youtube_ingest(
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                db_conn,
                _downloader=downloader,
            )

    def test_whisper_and_captions_both_fail(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """If both captions and Whisper fail, raises YouTubeTranscriptionError."""
        downloader = FakeDownloader(
            manual_captions=None,
            auto_captions=None,
            has_manual=False,
            fail_audio=True,
        )
        with pytest.raises((YouTubeTranscriptionError, YouTubeFetchError)):
            handle_youtube_ingest(
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                db_conn,
                _downloader=downloader,
                _transcriber=_fake_transcriber,
                _summarizer=_fake_summarizer_skip,
                _embedder=_fake_embedder,
            )

    def test_youtu_be_normalizes_correctly(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """youtu.be short links normalize to canonical form."""
        downloader = FakeDownloader(manual_captions=_GOOD_TRANSCRIPT, has_manual=True)
        result = handle_youtube_ingest(
            {"url": "https://youtu.be/dQw4w9WgXcQ"},
            db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        assert result["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_vault_file_has_correct_frontmatter(
        self, db_conn: sqlite3.Connection, vault_dir: Path
    ):
        """Vault file contains all expected frontmatter fields."""
        downloader = FakeDownloader(manual_captions=_GOOD_TRANSCRIPT, has_manual=True)
        handle_youtube_ingest(
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            db_conn,
            _downloader=downloader,
            _summarizer=_fake_summarizer_skip,
            _embedder=_fake_embedder,
        )
        md_files = list(vault_dir.rglob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "source: youtube" in content
        assert "url:" in content
        assert "title:" in content
        assert "channel:" in content
        assert "duration_s:" in content
        assert "caption_source:" in content
        assert "transcript_words:" in content
        assert "summarized:" in content
        assert "fetched_at:" in content
