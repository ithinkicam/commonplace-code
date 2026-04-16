"""Unit tests for commonplace_server.openlibrary."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from commonplace_server.openlibrary import (
    fetch_work_description,
    get_book_data,
    search_book,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(json_data: dict | list, status_code: int = 200) -> MagicMock:
    """Return a mock httpx.Response-like object."""
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


def _ol_search_response(works: list[dict]) -> dict:
    return {"numFound": len(works), "docs": works}


# ---------------------------------------------------------------------------
# search_book tests
# ---------------------------------------------------------------------------


def test_search_book_returns_first_doc() -> None:
    """search_book returns the first doc from the API response."""
    doc = {
        "key": "/works/OL123W",
        "title": "Dune",
        "author_name": ["Frank Herbert"],
        "first_publish_year": 1965,
        "isbn": ["9780441013593"],
        "subject": ["Science fiction"],
    }
    resp = _mock_response(_ol_search_response([doc]))

    with patch("httpx.get", return_value=resp) as mock_get:
        result = search_book("Dune", "Frank Herbert")

    assert result is not None
    assert result["key"] == "/works/OL123W"
    assert result["title"] == "Dune"
    # Verify correct endpoint used
    call_url = mock_get.call_args[0][0]
    assert "search.json" in call_url


def test_search_book_no_results_returns_none() -> None:
    """search_book returns None when API returns empty docs."""
    resp = _mock_response(_ol_search_response([]))

    with patch("httpx.get", return_value=resp):
        result = search_book("Nonexistent Book XYZ", "Nobody")

    assert result is None


def test_search_book_network_error_returns_none() -> None:
    """search_book returns None gracefully on network error."""
    with patch("httpx.get", side_effect=Exception("connection refused")):
        result = search_book("Dune")

    assert result is None


def test_search_book_empty_title_returns_none() -> None:
    """search_book returns None immediately for empty title."""
    result = search_book("")
    assert result is None


def test_search_book_author_optional() -> None:
    """search_book works with title only (no author)."""
    doc = {"key": "/works/OL456W", "title": "1984"}
    resp = _mock_response(_ol_search_response([doc]))

    with patch("httpx.get", return_value=resp):
        result = search_book("1984")

    assert result is not None
    assert result["key"] == "/works/OL456W"


# ---------------------------------------------------------------------------
# fetch_work_description tests
# ---------------------------------------------------------------------------


def test_fetch_work_description_plain_string() -> None:
    """fetch_work_description handles plain string description."""
    work_data = {"description": "A classic science fiction novel."}
    resp = _mock_response(work_data)

    with patch("httpx.get", return_value=resp):
        result = fetch_work_description("/works/OL123W")

    assert result == "A classic science fiction novel."


def test_fetch_work_description_typed_object() -> None:
    """fetch_work_description handles typed description object."""
    work_data = {
        "description": {
            "type": "/type/text",
            "value": "A sprawling space opera.",
        }
    }
    resp = _mock_response(work_data)

    with patch("httpx.get", return_value=resp):
        result = fetch_work_description("works/OL789W")

    assert result == "A sprawling space opera."


def test_fetch_work_description_missing_returns_none() -> None:
    """fetch_work_description returns None when description key absent."""
    work_data = {"title": "A Book Without Description"}
    resp = _mock_response(work_data)

    with patch("httpx.get", return_value=resp):
        result = fetch_work_description("/works/OL000W")

    assert result is None


def test_fetch_work_description_404_returns_none() -> None:
    """fetch_work_description returns None on 404."""
    from httpx import HTTPStatusError

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )

    with patch("httpx.get", return_value=resp):
        result = fetch_work_description("/works/OL_MISSING")

    assert result is None


def test_fetch_work_description_network_error_returns_none() -> None:
    """fetch_work_description returns None on network failure."""
    with patch("httpx.get", side_effect=Exception("timeout")):
        result = fetch_work_description("/works/OL123W")

    assert result is None


def test_fetch_work_description_key_normalisation() -> None:
    """fetch_work_description handles keys with or without leading slash."""
    work_data = {"description": "Some text."}
    resp = _mock_response(work_data)

    with patch("httpx.get", return_value=resp) as mock_get:
        # With leading slash
        fetch_work_description("/works/OL1W")
        url_with_slash = mock_get.call_args[0][0]

        # Without leading slash
        fetch_work_description("works/OL1W")
        url_without_slash = mock_get.call_args[0][0]

    assert url_with_slash == url_without_slash
    assert "works/OL1W" in url_with_slash


# ---------------------------------------------------------------------------
# get_book_data integration (search + fetch) tests
# ---------------------------------------------------------------------------


def test_get_book_data_happy_path() -> None:
    """get_book_data returns description, subjects, year, isbn."""
    search_doc = {
        "key": "/works/OL123W",
        "title": "Dune",
        "author_name": ["Frank Herbert"],
        "first_publish_year": 1965,
        "isbn": ["9780441013593", "0441013597"],
        "subject": ["Science fiction", "Desert planets"],
    }
    search_resp = _mock_response(_ol_search_response([search_doc]))
    work_resp = _mock_response({"description": "Set on the desert planet Arrakis."})

    with patch("httpx.get", side_effect=[search_resp, work_resp]):
        result = get_book_data("Dune", "Frank Herbert")

    assert result is not None
    assert result["description"] == "Set on the desert planet Arrakis."
    assert "Science fiction" in result["subjects"]
    assert result["first_published_year"] == 1965
    assert result["isbn"] == "9780441013593"  # 13-digit preferred
    assert result["source"] == "open_library"


def test_get_book_data_prefers_isbn13() -> None:
    """get_book_data prefers ISBN-13 over ISBN-10."""
    search_doc = {
        "key": "/works/OL1W",
        "title": "A Book",
        "isbn": ["0441013597", "9780441013593"],  # 10 first, then 13
        "subject": [],
    }
    search_resp = _mock_response(_ol_search_response([search_doc]))
    work_resp = _mock_response({"description": "Text."})

    with patch("httpx.get", side_effect=[search_resp, work_resp]):
        result = get_book_data("A Book")

    assert result is not None
    assert result["isbn"] == "9780441013593"


def test_get_book_data_not_found_returns_none() -> None:
    """get_book_data returns None when no search results."""
    resp = _mock_response(_ol_search_response([]))

    with patch("httpx.get", return_value=resp):
        result = get_book_data("zzz nonexistent zzz")

    assert result is None
