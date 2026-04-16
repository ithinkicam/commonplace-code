"""TMDB API client for Commonplace Phase 5b (video metadata enrichment).

Pure functions — no database I/O. All functions return None on failure (404,
network error, missing API key) rather than raising, so the caller can decide
whether to skip enrichment or mark the job failed.

API key resolution order:
  1. COMMONPLACE_TMDB_API_KEY environment variable
  2. macOS keychain item: service=commonplace-tmdb-api-key, account=tmdb
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
_REQUEST_TIMEOUT = 10.0  # seconds


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


def resolve_tmdb_api_key() -> str | None:
    """Resolve the TMDB API key.

    Checks COMMONPLACE_TMDB_API_KEY env var first, then falls back to
    reading from the macOS keychain via ``security find-generic-password``.

    Returns the key string, or None if neither source is configured.
    """
    env_val = os.environ.get("COMMONPLACE_TMDB_API_KEY")
    if env_val:
        return env_val

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "commonplace-tmdb-api-key",
                "-a",
                "tmdb",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            if token:
                return token
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("TMDB keychain lookup failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------


def search_movie(
    title: str,
    year: int | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any] | None:
    """Search TMDB for a movie.

    Parameters
    ----------
    title:
        Movie title to search for.
    year:
        Optional release year to narrow the search.
    api_key:
        TMDB API v3 key. If None, resolved via resolve_tmdb_api_key().

    Returns
    -------
    The top TMDB search result dict, or None if no match / error.
    """
    key = api_key or resolve_tmdb_api_key()
    if not key:
        logger.warning("TMDB key not configured — skipping enrichment")
        return None

    params: dict[str, Any] = {
        "api_key": key,
        "query": title,
        "include_adult": "false",
    }
    if year is not None:
        params["year"] = year

    try:
        resp = httpx.get(
            f"{TMDB_BASE_URL}/search/movie",
            params=params,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[dict[str, Any]] = data.get("results", [])
        if not results:
            return None
        return results[0]
    except httpx.HTTPStatusError as exc:
        logger.debug("TMDB movie search HTTP error %d: %s", exc.response.status_code, exc)
        return None
    except (httpx.RequestError, ValueError) as exc:
        logger.debug("TMDB movie search error: %s", exc)
        return None


def search_tv(
    title: str,
    year: int | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any] | None:
    """Search TMDB for a TV show.

    Parameters
    ----------
    title:
        Show title to search for.
    year:
        Optional first-air year to narrow the search.
    api_key:
        TMDB API v3 key. If None, resolved via resolve_tmdb_api_key().

    Returns
    -------
    The top TMDB search result dict, or None if no match / error.
    """
    key = api_key or resolve_tmdb_api_key()
    if not key:
        logger.warning("TMDB key not configured — skipping enrichment")
        return None

    params: dict[str, Any] = {
        "api_key": key,
        "query": title,
        "include_adult": "false",
    }
    if year is not None:
        params["first_air_date_year"] = year

    try:
        resp = httpx.get(
            f"{TMDB_BASE_URL}/search/tv",
            params=params,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[dict[str, Any]] = data.get("results", [])
        if not results:
            return None
        return results[0]
    except httpx.HTTPStatusError as exc:
        logger.debug("TMDB TV search HTTP error %d: %s", exc.response.status_code, exc)
        return None
    except (httpx.RequestError, ValueError) as exc:
        logger.debug("TMDB TV search error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Detail functions
# ---------------------------------------------------------------------------


def get_movie_details(
    tmdb_id: int,
    *,
    api_key: str | None = None,
) -> dict[str, Any] | None:
    """Fetch full movie details from TMDB, including credits for director.

    Parameters
    ----------
    tmdb_id:
        TMDB movie ID.
    api_key:
        TMDB API v3 key.

    Returns
    -------
    Movie detail dict with an injected 'director' key (str | None), or None on error.
    """
    key = api_key or resolve_tmdb_api_key()
    if not key:
        logger.warning("TMDB key not configured — skipping enrichment")
        return None

    try:
        resp = httpx.get(
            f"{TMDB_BASE_URL}/movie/{tmdb_id}",
            params={"api_key": key, "append_to_response": "credits"},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            logger.debug("TMDB movie %d not found", tmdb_id)
            return None
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        # Extract director from credits
        director: str | None = None
        credits = data.get("credits", {})
        for crew_member in credits.get("crew", []):
            if crew_member.get("job") == "Director":
                director = crew_member.get("name")
                break
        data["director"] = director

        return data
    except httpx.HTTPStatusError as exc:
        logger.debug("TMDB movie detail HTTP error %d: %s", exc.response.status_code, exc)
        return None
    except (httpx.RequestError, ValueError) as exc:
        logger.debug("TMDB movie detail error: %s", exc)
        return None


def get_tv_details(
    tmdb_id: int,
    *,
    api_key: str | None = None,
) -> dict[str, Any] | None:
    """Fetch full TV show details from TMDB.

    Parameters
    ----------
    tmdb_id:
        TMDB TV show ID.
    api_key:
        TMDB API v3 key.

    Returns
    -------
    TV show detail dict, or None on error.
    """
    key = api_key or resolve_tmdb_api_key()
    if not key:
        logger.warning("TMDB key not configured — skipping enrichment")
        return None

    try:
        resp = httpx.get(
            f"{TMDB_BASE_URL}/tv/{tmdb_id}",
            params={"api_key": key},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            logger.debug("TMDB TV show %d not found", tmdb_id)
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.debug("TMDB TV detail HTTP error %d: %s", exc.response.status_code, exc)
        return None
    except (httpx.RequestError, ValueError) as exc:
        logger.debug("TMDB TV detail error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------


def pick_best_movie_match(
    results_first: dict[str, Any] | None,
    parsed_year: int | None,
) -> dict[str, Any] | None:
    """Accept or reject the top TMDB result based on year proximity.

    Parameters
    ----------
    results_first:
        The first result from search_movie(), or None.
    parsed_year:
        The year extracted from the filename, or None.

    Returns
    -------
    The result dict if it's a plausible match, else None.
    """
    if results_first is None:
        return None
    if parsed_year is None:
        # No year to compare — accept on title match alone
        return results_first

    release_date: str = results_first.get("release_date", "") or ""
    if not release_date:
        return results_first  # no date on TMDB side — accept

    try:
        tmdb_year = int(release_date[:4])
    except (ValueError, IndexError):
        return results_first

    if abs(tmdb_year - parsed_year) <= 1:
        return results_first
    return None


def pick_best_tv_match(
    results_first: dict[str, Any] | None,
    parsed_year: int | None,
) -> dict[str, Any] | None:
    """Accept or reject the top TMDB result based on first-air year proximity.

    Parameters
    ----------
    results_first:
        The first result from search_tv(), or None.
    parsed_year:
        The year extracted from the filename, or None.

    Returns
    -------
    The result dict if it's a plausible match, else None.
    """
    if results_first is None:
        return None
    if parsed_year is None:
        return results_first

    first_air_date: str = results_first.get("first_air_date", "") or ""
    if not first_air_date:
        return results_first

    try:
        tmdb_year = int(first_air_date[:4])
    except (ValueError, IndexError):
        return results_first

    if abs(tmdb_year - parsed_year) <= 1:
        return results_first
    return None
