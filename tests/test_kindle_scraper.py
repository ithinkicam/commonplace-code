"""Tests for commonplace_worker/kindle_scraper.py.

Uses synthetic HTML fixtures — no real network calls. No real ASINs or
highlight content. All book/highlight data is invented for testing.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "kindle"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html(filename: str) -> str:
    return (FIXTURES / filename).read_text()


def _make_response(html: str, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response with the given HTML body."""
    return httpx.Response(
        status_code=status_code,
        content=html.encode(),
        headers={"content-type": "text/html; charset=utf-8"},
        request=httpx.Request("GET", "https://read.amazon.com/notebook"),
    )


# ---------------------------------------------------------------------------
# Library page parsing
# ---------------------------------------------------------------------------


class TestLibraryParsing:
    def test_parses_two_books(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_library_page

        books = _parse_library_page(_html("notebook_library.html"), "https://read.amazon.com/notebook")
        assert len(books) == 2

    def test_book_asin_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_library_page

        books = _parse_library_page(_html("notebook_library.html"), "https://read.amazon.com/notebook")
        asins = {b.asin for b in books}
        assert "B00TESTBOOK" in asins
        assert "B00ANOTHERB" in asins

    def test_book_title_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_library_page

        books = _parse_library_page(_html("notebook_library.html"), "https://read.amazon.com/notebook")
        book = next(b for b in books if b.asin == "B00TESTBOOK")
        assert book.title == "A Fictional Journey"

    def test_book_author_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_library_page

        books = _parse_library_page(_html("notebook_library.html"), "https://read.amazon.com/notebook")
        book = next(b for b in books if b.asin == "B00TESTBOOK")
        assert book.authors == "Jane Testwriter"

    def test_book_cover_url_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_library_page

        books = _parse_library_page(_html("notebook_library.html"), "https://read.amazon.com/notebook")
        book = next(b for b in books if b.asin == "B00TESTBOOK")
        assert book.cover_url is not None
        assert "fake-cover-1" in book.cover_url

    def test_empty_library_returns_empty_list(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_library_page

        books = _parse_library_page(_html("empty_library.html"), "https://read.amazon.com/notebook")
        assert books == []

    def test_login_redirect_raises_session_expired(self) -> None:
        from commonplace_worker.kindle_scraper import KindleSessionExpired, _parse_library_page

        with pytest.raises(KindleSessionExpired):
            _parse_library_page(_html("login_redirect.html"), "https://read.amazon.com/notebook")


# ---------------------------------------------------------------------------
# Highlights page parsing
# ---------------------------------------------------------------------------


class TestHighlightsParsing:
    def test_parses_three_highlights(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_highlights_page

        highlights = _parse_highlights_page(
            _html("notebook_highlights.html"),
            "https://read.amazon.com/notebook?asin=B00TESTBOOK",
            "B00TESTBOOK",
        )
        assert len(highlights) == 3

    def test_highlight_text_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_highlights_page

        highlights = _parse_highlights_page(
            _html("notebook_highlights.html"),
            "https://read.amazon.com/notebook?asin=B00TESTBOOK",
            "B00TESTBOOK",
        )
        assert highlights[0].text == "This is a synthetic highlight for testing purposes only."

    def test_highlight_note_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_highlights_page

        highlights = _parse_highlights_page(
            _html("notebook_highlights.html"),
            "https://read.amazon.com/notebook?asin=B00TESTBOOK",
            "B00TESTBOOK",
        )
        # Second highlight has a note
        hl_with_note = next(h for h in highlights if h.note is not None)
        assert "reader note" in hl_with_note.note

    def test_highlight_without_note_is_none(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_highlights_page

        highlights = _parse_highlights_page(
            _html("notebook_highlights.html"),
            "https://read.amazon.com/notebook?asin=B00TESTBOOK",
            "B00TESTBOOK",
        )
        assert highlights[0].note is None

    def test_highlight_location_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_highlights_page

        highlights = _parse_highlights_page(
            _html("notebook_highlights.html"),
            "https://read.amazon.com/notebook?asin=B00TESTBOOK",
            "B00TESTBOOK",
        )
        assert highlights[0].location == "Location 142"

    def test_highlight_color_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_highlights_page

        highlights = _parse_highlights_page(
            _html("notebook_highlights.html"),
            "https://read.amazon.com/notebook?asin=B00TESTBOOK",
            "B00TESTBOOK",
        )
        assert highlights[0].color == "yellow"

    def test_highlight_timestamp_extracted(self) -> None:
        from commonplace_worker.kindle_scraper import _parse_highlights_page

        highlights = _parse_highlights_page(
            _html("notebook_highlights.html"),
            "https://read.amazon.com/notebook?asin=B00TESTBOOK",
            "B00TESTBOOK",
        )
        assert highlights[0].created_at is not None
        assert "2023" in highlights[0].created_at

    def test_login_redirect_raises_session_expired(self) -> None:
        from commonplace_worker.kindle_scraper import KindleSessionExpired, _parse_highlights_page

        with pytest.raises(KindleSessionExpired):
            _parse_highlights_page(
                _html("login_redirect.html"),
                "https://read.amazon.com/notebook?asin=B00TESTBOOK",
                "B00TESTBOOK",
            )


# ---------------------------------------------------------------------------
# KindleStructureChanged — broken selector detection
# ---------------------------------------------------------------------------


class TestStructureChanged:
    def test_require_raises_on_empty(self) -> None:
        from commonplace_worker.kindle_scraper import KindleStructureChanged, _require

        with pytest.raises(KindleStructureChanged, match="KINDLE_SELECTOR_BROKEN"):
            _require([], "div.some-class", "https://example.com")

    def test_require_raises_names_selector(self) -> None:
        from commonplace_worker.kindle_scraper import KindleStructureChanged, _require

        selector = "div.kp-notebook-highlight-container"
        with pytest.raises(KindleStructureChanged, match=selector):
            _require([], selector, "https://read.amazon.com/notebook?asin=FAKE")

    def test_require_passes_nonempty(self) -> None:
        from commonplace_worker.kindle_scraper import _require

        result = _require(["elem1", "elem2"], "div.some-class", "https://example.com")
        assert result == ["elem1", "elem2"]

    def test_broken_highlights_page_raises_structure_changed(self) -> None:
        """A page with content but no annotations container and >200 chars raises KindleStructureChanged."""
        from commonplace_worker.kindle_scraper import KindleStructureChanged, _parse_highlights_page

        with pytest.raises(KindleStructureChanged):
            _parse_highlights_page(
                _html("broken_structure.html"),
                "https://read.amazon.com/notebook?asin=B00TESTBOOK",
                "B00TESTBOOK",
            )


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_rate_limiter_sleeps_on_fast_calls(self) -> None:
        from commonplace_worker.kindle_scraper import _RateLimiter

        sleep_calls: list[float] = []

        with patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)):
            limiter = _RateLimiter(min_delay=1.5, jitter=0.0)
            # First call: no prior request, last_request is 0 (very old) — may sleep
            # Set last_request to now to simulate immediate second call
            limiter._last_request = time.monotonic()
            limiter.wait()

        assert len(sleep_calls) >= 1
        assert sleep_calls[0] > 0

    def test_rate_limiter_no_sleep_after_long_gap(self) -> None:
        from commonplace_worker.kindle_scraper import _RateLimiter

        sleep_calls: list[float] = []

        with patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)):
            limiter = _RateLimiter(min_delay=1.5, jitter=0.0)
            # last_request is 0 (epoch) — enough time has passed, no sleep needed
            limiter.wait()

        # Should not have slept because enough time has elapsed since epoch
        assert len(sleep_calls) == 0

    def test_rate_limiter_increments_count(self) -> None:
        from commonplace_worker.kindle_scraper import _RateLimiter

        with patch("time.sleep"):
            limiter = _RateLimiter(min_delay=0.0, jitter=0.0)
            limiter.wait()
            limiter.wait()
            limiter.wait()

        assert limiter.count == 3


# ---------------------------------------------------------------------------
# fetch_library and fetch_highlights — mocked HTTP
# ---------------------------------------------------------------------------


class TestFetchFunctions:
    def test_fetch_library_calls_notebook_url(self) -> None:
        from commonplace_worker.kindle_scraper import fetch_library

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = _make_response(_html("notebook_library.html"))

            fake_cookies = httpx.Cookies()
            books = fetch_library(_cookies=fake_cookies)

        assert len(books) == 2
        mock_client.get.assert_called_once_with("https://read.amazon.com/notebook")

    def test_fetch_highlights_calls_asin_url(self) -> None:
        from commonplace_worker.kindle_scraper import fetch_highlights

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = _make_response(_html("notebook_highlights.html"))

            fake_cookies = httpx.Cookies()
            highlights = fetch_highlights("B00TESTBOOK", _cookies=fake_cookies)

        assert len(highlights) == 3
        call_url = mock_client.get.call_args[0][0]
        assert "B00TESTBOOK" in call_url

    def test_fetch_library_raises_cap_exceeded(self) -> None:
        from commonplace_worker.kindle_scraper import REQUEST_CAP, KindleCapExceeded, fetch_library

        count_ref = [REQUEST_CAP]  # already at cap
        fake_cookies = httpx.Cookies()

        with pytest.raises(KindleCapExceeded):
            fetch_library(_cookies=fake_cookies, _request_count=count_ref)

    def test_fetch_highlights_raises_cap_exceeded(self) -> None:
        from commonplace_worker.kindle_scraper import (
            REQUEST_CAP,
            KindleCapExceeded,
            fetch_highlights,
        )

        count_ref = [REQUEST_CAP]
        fake_cookies = httpx.Cookies()

        with pytest.raises(KindleCapExceeded):
            fetch_highlights("B00TESTBOOK", _cookies=fake_cookies, _request_count=count_ref)


# ---------------------------------------------------------------------------
# Cookie loading — mocked subprocess
# ---------------------------------------------------------------------------


class TestCookieLoading:
    def test_load_cookies_raises_if_keychain_missing(self) -> None:
        from commonplace_worker.kindle_scraper import (
            KindleCookiesMissing,
            load_cookies_from_keychain,
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=44, stdout="", stderr="not found")
            with pytest.raises(KindleCookiesMissing):
                load_cookies_from_keychain()

    def test_load_cookies_parses_json(self) -> None:
        from commonplace_worker.kindle_scraper import load_cookies_from_keychain

        cookie_json = '[{"name": "session-id", "value": "fake-session", "domain": ".amazon.com"}]'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=cookie_json, stderr="")
            cookies = load_cookies_from_keychain()

        # Should have at least one cookie
        assert cookies is not None

    def test_load_cookies_filters_non_amazon_domains(self) -> None:
        from commonplace_worker.kindle_scraper import load_cookies_from_keychain

        cookie_json = (
            '['
            '{"name": "good-cookie", "value": "v1", "domain": ".amazon.com"},'
            '{"name": "bad-cookie", "value": "v2", "domain": "example.com"}'
            ']'
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=cookie_json, stderr="")
            cookies = load_cookies_from_keychain()

        # Only amazon.com cookies should be present
        # Verify bad-cookie is not present by checking the jar
        cookie_names = {name for name, _ in cookies.items()}
        assert "good-cookie" in cookie_names
        assert "bad-cookie" not in cookie_names

    def test_load_cookies_raises_on_bad_json(self) -> None:
        from commonplace_worker.kindle_scraper import (
            KindleCookiesMissing,
            load_cookies_from_keychain,
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not-json", stderr="")
            with pytest.raises(KindleCookiesMissing, match="valid JSON"):
                load_cookies_from_keychain()
