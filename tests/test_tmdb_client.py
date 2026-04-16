"""Tests for commonplace_server/tmdb.py.

All TMDB HTTP calls are mocked — no live network requests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from commonplace_server.tmdb import (
    get_movie_details,
    get_tv_details,
    pick_best_movie_match,
    pick_best_tv_match,
    resolve_tmdb_api_key,
    search_movie,
    search_tv,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure COMMONPLACE_TMDB_API_KEY is not set unless tests set it."""
    monkeypatch.delenv("COMMONPLACE_TMDB_API_KEY", raising=False)


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# resolve_tmdb_api_key
# ---------------------------------------------------------------------------


def test_resolve_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key-123")
    assert resolve_tmdb_api_key() == "test-key-123"


def test_resolve_api_key_missing_returns_none() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = resolve_tmdb_api_key()
    assert result is None


def test_resolve_api_key_from_keychain() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="keychain-key\n")
        result = resolve_tmdb_api_key()
    assert result == "keychain-key"


# ---------------------------------------------------------------------------
# search_movie
# ---------------------------------------------------------------------------


def test_search_movie_returns_top_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    fake_result = {"id": 101, "title": "Toy Story", "release_date": "1995-11-22"}
    mock_resp = _mock_response({"results": [fake_result, {"id": 999}]})

    with patch("httpx.get", return_value=mock_resp):
        result = search_movie("Toy Story", 1995)

    assert result is not None
    assert result["id"] == 101
    assert result["title"] == "Toy Story"


def test_search_movie_no_results_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    mock_resp = _mock_response({"results": []})

    with patch("httpx.get", return_value=mock_resp):
        result = search_movie("NonExistentMovie12345")

    assert result is None


def test_search_movie_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")

    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        result = search_movie("Toy Story")

    assert result is None


def test_search_movie_no_api_key_returns_none() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = search_movie("Toy Story")
    assert result is None


def test_search_movie_http_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    mock_resp = _mock_response({}, status_code=401)

    with patch("httpx.get", return_value=mock_resp):
        result = search_movie("Toy Story")

    assert result is None


# ---------------------------------------------------------------------------
# search_tv
# ---------------------------------------------------------------------------


def test_search_tv_returns_top_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    fake_result = {"id": 202, "name": "Andor", "first_air_date": "2022-09-21"}
    mock_resp = _mock_response({"results": [fake_result]})

    with patch("httpx.get", return_value=mock_resp):
        result = search_tv("Andor", 2022)

    assert result is not None
    assert result["id"] == 202


def test_search_tv_no_results_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    mock_resp = _mock_response({"results": []})

    with patch("httpx.get", return_value=mock_resp):
        result = search_tv("NoSuchShow99999")

    assert result is None


def test_search_tv_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")

    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        result = search_tv("Andor")

    assert result is None


# ---------------------------------------------------------------------------
# get_movie_details
# ---------------------------------------------------------------------------


def test_get_movie_details_extracts_director(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    fake_data = {
        "id": 101,
        "title": "Toy Story",
        "overview": "A cowboy doll is profoundly threatened...",
        "genres": [{"id": 16, "name": "Animation"}],
        "release_date": "1995-11-22",
        "credits": {
            "crew": [
                {"job": "Producer", "name": "Some Producer"},
                {"job": "Director", "name": "John Lasseter"},
            ]
        },
    }
    mock_resp = _mock_response(fake_data)

    with patch("httpx.get", return_value=mock_resp):
        result = get_movie_details(101)

    assert result is not None
    assert result["director"] == "John Lasseter"
    assert result["overview"] == "A cowboy doll is profoundly threatened..."


def test_get_movie_details_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    mock_resp.raise_for_status.return_value = None  # not called; 404 handled before

    with patch("httpx.get", return_value=mock_resp):
        result = get_movie_details(999999)

    assert result is None


def test_get_movie_details_no_director(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    fake_data = {
        "id": 101,
        "title": "Test Movie",
        "overview": "Plot here.",
        "genres": [],
        "credits": {"crew": []},
    }
    mock_resp = _mock_response(fake_data)

    with patch("httpx.get", return_value=mock_resp):
        result = get_movie_details(101)

    assert result is not None
    assert result["director"] is None


def test_get_movie_details_no_api_key_returns_none() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_movie_details(101)
    assert result is None


# ---------------------------------------------------------------------------
# get_tv_details
# ---------------------------------------------------------------------------


def test_get_tv_details_returns_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    fake_data = {
        "id": 202,
        "name": "Andor",
        "overview": "Set in the Star Wars universe...",
        "genres": [{"id": 18, "name": "Drama"}, {"id": 10759, "name": "Action & Adventure"}],
        "first_air_date": "2022-09-21",
        "number_of_seasons": 2,
    }
    mock_resp = _mock_response(fake_data)

    with patch("httpx.get", return_value=mock_resp):
        result = get_tv_details(202)

    assert result is not None
    assert result["name"] == "Andor"
    assert result["number_of_seasons"] == 2


def test_get_tv_details_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.get", return_value=mock_resp):
        result = get_tv_details(999999)

    assert result is None


def test_get_tv_details_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_TMDB_API_KEY", "test-key")

    with patch("httpx.get", side_effect=httpx.RequestError("error")):
        result = get_tv_details(202)

    assert result is None


# ---------------------------------------------------------------------------
# pick_best_movie_match
# ---------------------------------------------------------------------------


def test_pick_best_movie_match_exact_year() -> None:
    result = {"id": 1, "release_date": "1995-11-22"}
    assert pick_best_movie_match(result, 1995) is result


def test_pick_best_movie_match_year_within_one() -> None:
    result = {"id": 1, "release_date": "1995-11-22"}
    assert pick_best_movie_match(result, 1996) is result


def test_pick_best_movie_match_year_too_far_returns_none() -> None:
    result = {"id": 1, "release_date": "1995-11-22"}
    assert pick_best_movie_match(result, 2000) is None


def test_pick_best_movie_match_no_parsed_year() -> None:
    """No filename year — accept on title match alone."""
    result = {"id": 1, "release_date": "1995-11-22"}
    assert pick_best_movie_match(result, None) is result


def test_pick_best_movie_match_none_result() -> None:
    assert pick_best_movie_match(None, 1995) is None


def test_pick_best_movie_match_no_tmdb_date() -> None:
    """TMDB result has no release_date — accept."""
    result = {"id": 1, "release_date": ""}
    assert pick_best_movie_match(result, 1995) is result


# ---------------------------------------------------------------------------
# pick_best_tv_match
# ---------------------------------------------------------------------------


def test_pick_best_tv_match_exact_year() -> None:
    result = {"id": 1, "first_air_date": "2022-09-21"}
    assert pick_best_tv_match(result, 2022) is result


def test_pick_best_tv_match_year_off_by_one() -> None:
    result = {"id": 1, "first_air_date": "2022-09-21"}
    assert pick_best_tv_match(result, 2021) is result


def test_pick_best_tv_match_year_too_far() -> None:
    result = {"id": 1, "first_air_date": "2022-09-21"}
    assert pick_best_tv_match(result, 2015) is None


def test_pick_best_tv_match_no_parsed_year() -> None:
    result = {"id": 1, "first_air_date": "2022-09-21"}
    assert pick_best_tv_match(result, None) is result
