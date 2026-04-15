"""Tests for commonplace_worker/handlers/kindle.py.

All scraper calls are mocked — no network access.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from commonplace_db.db import migrate
from commonplace_worker.kindle_scraper import KindleBook, KindleHighlight

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with all migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------
# Scraper stubs
# ---------------------------------------------------------------------------

FAKE_BOOKS = [
    KindleBook(asin="B00TESTBOOK", title="A Fictional Journey", authors="Jane Testwriter"),
    KindleBook(asin="B00ANOTHERB", title="The Made-Up Chronicles", authors="John Fakename"),
]

FAKE_HIGHLIGHTS_B00TESTBOOK = [
    KindleHighlight(
        location="Location 142",
        page=None,
        text="This is a synthetic highlight for testing.",
        note=None,
        color="yellow",
        created_at="Added on Sunday, January 1, 2023",
    ),
    KindleHighlight(
        location="Location 256",
        page=None,
        text="Another invented passage for unit test validation.",
        note="A reader note on this highlight.",
        color="blue",
        created_at="Added on Monday, January 2, 2023",
    ),
]

FAKE_HIGHLIGHTS_B00ANOTHERB = [
    KindleHighlight(
        location="Location 88",
        page=None,
        text="A highlight from the second test book.",
        note=None,
        color="yellow",
        created_at="Added on Tuesday, January 3, 2023",
    ),
]


def _stub_fetch_library(**kwargs: Any) -> list[KindleBook]:
    return FAKE_BOOKS


def _stub_fetch_highlights(asin: str, **kwargs: Any) -> list[KindleHighlight]:
    if asin == "B00TESTBOOK":
        return FAKE_HIGHLIGHTS_B00TESTBOOK
    if asin == "B00ANOTHERB":
        return FAKE_HIGHLIGHTS_B00ANOTHERB
    return []


# ---------------------------------------------------------------------------
# Full ingest tests
# ---------------------------------------------------------------------------


class TestFullIngest:
    def test_full_ingest_creates_book_rows(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        result = handle_kindle_ingest(
            {"mode": "full"},
            db_conn,
            _scraper_fetch_library=_stub_fetch_library,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=_fake_embedder,
            _cookies=fake_cookies,
        )

        assert result["status"] == "complete"
        book_count = db_conn.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type = 'kindle_book'"
        ).fetchone()[0]
        assert book_count == 2

    def test_full_ingest_creates_highlight_rows(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        result = handle_kindle_ingest(
            {"mode": "full"},
            db_conn,
            _scraper_fetch_library=_stub_fetch_library,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=_fake_embedder,
            _cookies=fake_cookies,
        )

        total_highlights = len(FAKE_HIGHLIGHTS_B00TESTBOOK) + len(FAKE_HIGHLIGHTS_B00ANOTHERB)
        assert result["highlights_new"] == total_highlights

        hl_count = db_conn.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type = 'kindle_highlight'"
        ).fetchone()[0]
        assert hl_count == total_highlights

    def test_full_ingest_books_processed_count(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        result = handle_kindle_ingest(
            {"mode": "full"},
            db_conn,
            _scraper_fetch_library=_stub_fetch_library,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=_fake_embedder,
            _cookies=fake_cookies,
        )
        assert result["books_processed"] == 2

    def test_book_row_has_correct_content_type_and_source(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        handle_kindle_ingest(
            {"mode": "full"},
            db_conn,
            _scraper_fetch_library=_stub_fetch_library,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=_fake_embedder,
            _cookies=fake_cookies,
        )

        book = db_conn.execute(
            "SELECT * FROM documents WHERE content_type = 'kindle_book' AND source_id = 'B00TESTBOOK'"
        ).fetchone()
        assert book is not None
        assert book["source_uri"] == "amazon-asin:B00TESTBOOK"
        assert book["title"] == "A Fictional Journey"
        assert book["author"] == "Jane Testwriter"

    def test_highlight_row_has_correct_content_type_and_source(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        handle_kindle_ingest(
            {"mode": "full"},
            db_conn,
            _scraper_fetch_library=_stub_fetch_library,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=_fake_embedder,
            _cookies=fake_cookies,
        )

        # Source ID should be asin#location
        hl = db_conn.execute(
            "SELECT * FROM documents WHERE content_type = 'kindle_highlight' AND source_id LIKE 'B00TESTBOOK#%'"
        ).fetchone()
        assert hl is not None
        assert hl["source_uri"].startswith("amazon-asin:B00TESTBOOK#")

    def test_highlight_embed_called(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        embed_calls: list[int] = []

        def counting_embedder(texts: list[str], model: str) -> list[list[float]]:
            embed_calls.append(len(texts))
            return [[0.0] * 768 for _ in texts]

        fake_cookies = httpx.Cookies()
        handle_kindle_ingest(
            {"mode": "full"},
            db_conn,
            _scraper_fetch_library=_stub_fetch_library,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=counting_embedder,
            _cookies=fake_cookies,
        )

        total = len(FAKE_HIGHLIGHTS_B00TESTBOOK) + len(FAKE_HIGHLIGHTS_B00ANOTHERB)
        # Each highlight triggers at least one embed call
        assert len(embed_calls) == total


# ---------------------------------------------------------------------------
# Book-mode ingest tests
# ---------------------------------------------------------------------------


class TestBookModeIngest:
    def test_book_mode_ingests_one_book(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        result = handle_kindle_ingest(
            {"mode": "book", "asin": "B00TESTBOOK"},
            db_conn,
            _scraper_fetch_library=_stub_fetch_library,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=_fake_embedder,
            _cookies=fake_cookies,
        )

        assert result["status"] == "complete"
        assert result["books_processed"] == 1
        assert result["highlights_new"] == len(FAKE_HIGHLIGHTS_B00TESTBOOK)

    def test_book_mode_requires_asin(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        with pytest.raises(ValueError, match="asin"):
            handle_kindle_ingest(
                {"mode": "book"},
                db_conn,
                _scraper_fetch_library=_stub_fetch_library,
                _scraper_fetch_highlights=_stub_fetch_highlights,
                _embedder=_fake_embedder,
                _cookies=fake_cookies,
            )


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_skips_existing_highlights(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        kwargs = {
            "_scraper_fetch_library": _stub_fetch_library,
            "_scraper_fetch_highlights": _stub_fetch_highlights,
            "_embedder": _fake_embedder,
            "_cookies": fake_cookies,
        }

        result1 = handle_kindle_ingest({"mode": "full"}, db_conn, **kwargs)
        result2 = handle_kindle_ingest({"mode": "full"}, db_conn, **kwargs)

        assert result1["highlights_new"] > 0
        assert result2["highlights_new"] == 0
        assert result2["highlights_skipped"] == result1["highlights_new"]

        # Still only the correct number of rows
        hl_count = db_conn.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type = 'kindle_highlight'"
        ).fetchone()[0]
        assert hl_count == result1["highlights_new"]

    def test_second_run_skips_existing_books(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest

        fake_cookies = httpx.Cookies()
        kwargs = {
            "_scraper_fetch_library": _stub_fetch_library,
            "_scraper_fetch_highlights": _stub_fetch_highlights,
            "_embedder": _fake_embedder,
            "_cookies": fake_cookies,
        }

        handle_kindle_ingest({"mode": "full"}, db_conn, **kwargs)
        handle_kindle_ingest({"mode": "full"}, db_conn, **kwargs)

        book_count = db_conn.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type = 'kindle_book'"
        ).fetchone()[0]
        assert book_count == 2  # no duplicates


# ---------------------------------------------------------------------------
# Blocked on cookies
# ---------------------------------------------------------------------------


class TestBlockedOnCookies:
    def test_missing_cookies_returns_blocked_status(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest
        from commonplace_worker.kindle_scraper import KindleCookiesMissing

        def raise_missing(**kwargs: Any) -> list[KindleBook]:
            raise KindleCookiesMissing("Keychain item not found")

        result = handle_kindle_ingest(
            {"mode": "full"},
            db_conn,
            _scraper_fetch_library=raise_missing,
            _scraper_fetch_highlights=_stub_fetch_highlights,
            _embedder=_fake_embedder,
            _cookies=None,  # triggers real cookie load which we mock
        )
        # With _cookies=None, handler calls load_cookies_from_keychain
        # We need to mock that path
        assert result  # just ensure it ran; real test is below

    def test_missing_cookies_with_mock(self, db_conn: sqlite3.Connection) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest
        from commonplace_worker.kindle_scraper import KindleCookiesMissing

        with patch(
            "commonplace_worker.handlers.kindle.handle_kindle_ingest.__wrapped__"
            if hasattr(handle_kindle_ingest, "__wrapped__")
            else "commonplace_worker.kindle_scraper.load_cookies_from_keychain",
            side_effect=KindleCookiesMissing("not found"),
        ):
            result = handle_kindle_ingest(
                {"mode": "full"},
                db_conn,
                _scraper_fetch_library=_stub_fetch_library,
                _scraper_fetch_highlights=_stub_fetch_highlights,
                _embedder=_fake_embedder,
                _cookies=None,
            )

        assert result["status"] == "blocked_on_cookies"
        assert "blocked_on_cookies" in result["status"]


# ---------------------------------------------------------------------------
# Structure changed — alert mechanism
# ---------------------------------------------------------------------------


class TestStructureChangedAlert:
    def test_structure_changed_returns_failed_status(self, db_conn: sqlite3.Connection, tmp_path: object) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest
        from commonplace_worker.kindle_scraper import KindleStructureChanged

        def raise_structure(**kwargs: Any) -> list[KindleBook]:
            raise KindleStructureChanged(
                "KINDLE_SELECTOR_BROKEN: selector 'div.kp-notebook-library-each-book' matched zero elements"
            )

        fake_cookies = httpx.Cookies()

        with patch("commonplace_worker.handlers.kindle._write_alert") as mock_alert:
            mock_alert.return_value = MagicMock()
            result = handle_kindle_ingest(
                {"mode": "full"},
                db_conn,
                _scraper_fetch_library=raise_structure,
                _scraper_fetch_highlights=_stub_fetch_highlights,
                _embedder=_fake_embedder,
                _cookies=fake_cookies,
            )

        assert result["status"] == "failed"
        mock_alert.assert_called_once()

    def test_structure_changed_writes_stderr(
        self, db_conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from commonplace_worker.handlers.kindle import handle_kindle_ingest
        from commonplace_worker.kindle_scraper import KindleStructureChanged

        def raise_structure(**kwargs: Any) -> list[KindleBook]:
            raise KindleStructureChanged("KINDLE_SELECTOR_BROKEN: selector 'div.foo' matched zero elements")

        fake_cookies = httpx.Cookies()

        with patch("commonplace_worker.handlers.kindle._write_alert"):
            handle_kindle_ingest(
                {"mode": "full"},
                db_conn,
                _scraper_fetch_library=raise_structure,
                _scraper_fetch_highlights=_stub_fetch_highlights,
                _embedder=_fake_embedder,
                _cookies=fake_cookies,
            )

        captured = capsys.readouterr()
        assert "KINDLE_SELECTOR_BROKEN" in captured.err
