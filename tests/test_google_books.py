"""Unit tests for commonplace_server.google_books."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from commonplace_server.google_books import (
    _cache_key,
    _extract_isbn,
    _extract_year,
    get_book_data,
    search_book,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        from httpx import HTTPStatusError

        resp.raise_for_status.side_effect = HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _gb_response(items: list[dict]) -> dict:
    return {"totalItems": len(items), "items": items}


def _make_volume(
    title: str = "Test Book",
    authors: list[str] | None = None,
    description: str = "A book.",
    categories: list[str] | None = None,
    published_date: str = "2001",
    isbn13: str = "9780000000001",
    isbn10: str | None = None,
) -> dict:
    identifiers = []
    if isbn13:
        identifiers.append({"type": "ISBN_13", "identifier": isbn13})
    if isbn10:
        identifiers.append({"type": "ISBN_10", "identifier": isbn10})

    return {
        "volumeInfo": {
            "title": title,
            "authors": authors or ["Test Author"],
            "description": description,
            "categories": categories or ["Fiction"],
            "publishedDate": published_date,
            "industryIdentifiers": identifiers,
        }
    }


# ---------------------------------------------------------------------------
# search_book tests
# ---------------------------------------------------------------------------


def test_search_book_returns_volume_info() -> None:
    """search_book returns volumeInfo from the first item."""
    vol = _make_volume(title="Dune", isbn13="9780441013593")
    resp = _mock_response(_gb_response([vol]))

    with patch("httpx.get", return_value=resp):
        result = search_book("Dune", "Frank Herbert")

    assert result is not None
    assert result["title"] == "Dune"


def test_search_book_no_results_returns_none() -> None:
    """search_book returns None when API returns no items."""
    resp = _mock_response({"totalItems": 0})

    with patch("httpx.get", return_value=resp):
        result = search_book("Totally Nonexistent Book XYZ")

    assert result is None


def test_search_book_network_error_returns_none() -> None:
    """search_book returns None gracefully on network error."""
    with patch("httpx.get", side_effect=Exception("timeout")):
        result = search_book("Dune")

    assert result is None


def test_search_book_empty_title_returns_none() -> None:
    """search_book returns None for empty title."""
    result = search_book("")
    assert result is None


# ---------------------------------------------------------------------------
# _extract_isbn tests
# ---------------------------------------------------------------------------


def test_extract_isbn_prefers_isbn13() -> None:
    """_extract_isbn prefers ISBN_13 over ISBN_10."""
    vol_info = {
        "industryIdentifiers": [
            {"type": "ISBN_10", "identifier": "0441013597"},
            {"type": "ISBN_13", "identifier": "9780441013593"},
        ]
    }
    assert _extract_isbn(vol_info) == "9780441013593"


def test_extract_isbn_falls_back_to_isbn10() -> None:
    """_extract_isbn falls back to ISBN_10 when ISBN_13 absent."""
    vol_info = {
        "industryIdentifiers": [
            {"type": "ISBN_10", "identifier": "0441013597"},
        ]
    }
    assert _extract_isbn(vol_info) == "0441013597"


def test_extract_isbn_no_identifiers() -> None:
    """_extract_isbn returns None when no identifiers present."""
    assert _extract_isbn({}) is None
    assert _extract_isbn({"industryIdentifiers": []}) is None


# ---------------------------------------------------------------------------
# _extract_year tests
# ---------------------------------------------------------------------------


def test_extract_year_full_date() -> None:
    """_extract_year handles YYYY-MM-DD format."""
    assert _extract_year({"publishedDate": "1965-08-01"}) == 1965


def test_extract_year_year_only() -> None:
    """_extract_year handles YYYY-only format."""
    assert _extract_year({"publishedDate": "2001"}) == 2001


def test_extract_year_missing() -> None:
    """_extract_year returns None when publishedDate absent."""
    assert _extract_year({}) is None


# ---------------------------------------------------------------------------
# get_book_data integration tests
# ---------------------------------------------------------------------------


def test_get_book_data_happy_path() -> None:
    """get_book_data returns enriched data from Google Books."""
    vol = _make_volume(
        title="1984",
        authors=["George Orwell"],
        description="A dystopian novel.",
        categories=["Fiction", "Political fiction"],
        published_date="1949",
        isbn13="9780451524935",
    )
    resp = _mock_response(_gb_response([vol]))

    with (
        patch("httpx.get", return_value=resp),
        patch("commonplace_server.google_books._load_cache", return_value=None),
        patch("commonplace_server.google_books._save_cache"),
    ):
        result = get_book_data("1984", "George Orwell")

    assert result is not None
    assert result["description"] == "A dystopian novel."
    assert "Fiction" in result["subjects"]
    assert result["first_published_year"] == 1949
    assert result["isbn"] == "9780451524935"
    assert result["source"] == "google_books"


def test_get_book_data_uses_cache(tmp_path: Path) -> None:
    """get_book_data returns cached data without hitting the API."""
    cached = {
        "description": "Cached description.",
        "subjects": ["Sci-fi"],
        "first_published_year": 1965,
        "isbn": "9780441013593",
        "source": "google_books",
    }

    with (
        patch("commonplace_server.google_books._load_cache", return_value=cached),
        patch("httpx.get") as mock_get,
    ):
        result = get_book_data("Dune", "Frank Herbert")

    assert result == cached
    mock_get.assert_not_called()


def test_get_book_data_saves_to_cache() -> None:
    """get_book_data saves result to cache after successful API call."""
    vol = _make_volume(description="Some text.", isbn13="9780000000001")
    resp = _mock_response(_gb_response([vol]))
    saved: list[dict] = []

    with (
        patch("httpx.get", return_value=resp),
        patch("commonplace_server.google_books._load_cache", return_value=None),
        patch("commonplace_server.google_books._save_cache", side_effect=lambda k, d: saved.append(d)),
    ):
        get_book_data("Some Book")

    assert len(saved) == 1
    assert saved[0]["description"] == "Some text."


def test_get_book_data_not_found_returns_none() -> None:
    """get_book_data returns None when no results."""
    resp = _mock_response({"totalItems": 0})

    with (
        patch("httpx.get", return_value=resp),
        patch("commonplace_server.google_books._load_cache", return_value=None),
    ):
        result = get_book_data("ZZZ Nonexistent ZZZ")

    assert result is None


def test_get_book_data_empty_title_returns_none() -> None:
    """get_book_data returns None for empty title without hitting API."""
    with patch("httpx.get") as mock_get:
        result = get_book_data("")
    assert result is None
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# _cache_key tests
# ---------------------------------------------------------------------------


def test_cache_key_deterministic() -> None:
    """_cache_key returns the same value for the same inputs."""
    k1 = _cache_key("Dune", "Frank Herbert")
    k2 = _cache_key("Dune", "Frank Herbert")
    assert k1 == k2


def test_cache_key_different_inputs() -> None:
    """_cache_key returns different values for different inputs."""
    k1 = _cache_key("Dune", "Frank Herbert")
    k2 = _cache_key("Foundation", "Isaac Asimov")
    assert k1 != k2


def test_cache_key_no_author() -> None:
    """_cache_key works when author is None."""
    k = _cache_key("Dune", None)
    assert isinstance(k, str)
    assert len(k) == 32
