"""Open Library public API client.

Pure functions; no API key required.  Uses httpx for HTTP.

Primary flow:
1. search_book(title, author) → dict | None
   Calls /search.json?q=...&limit=1 and returns the first hit or None.

2. fetch_work_description(work_key) → str | None
   Calls /works/{key}.json and extracts description (string or typed object).

Graceful None on 404, network errors, or missing data.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://openlibrary.org"
_TIMEOUT = 10.0


def search_book(title: str, author: str | None = None) -> dict[str, Any] | None:
    """Search Open Library for a book by title (and optionally author).

    Returns the first matching document dict from the search API, or None on
    failure / no results.

    The returned dict contains at least:
        - key: str   (e.g. "/works/OL123W")
        - title: str
        - author_name: list[str] | missing
        - first_publish_year: int | missing
        - isbn: list[str] | missing
        - subject: list[str] | missing
    """
    if not title:
        return None

    query_parts = [title]
    if author:
        query_parts.append(author)
    query = " ".join(query_parts)

    params = {"q": query, "limit": 1, "fields": "key,title,author_name,first_publish_year,isbn,subject"}

    try:
        resp = httpx.get(f"{_BASE}/search.json", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Open Library search failed for %r: %s", title, exc)
        return None

    docs = data.get("docs", [])
    if not docs:
        logger.debug("Open Library: no results for %r", title)
        return None

    return docs[0]


def fetch_work_description(work_key: str) -> str | None:
    """Fetch the description for a work from Open Library /works/{key}.json.

    Descriptions come in two shapes:
    - Plain string: "A novel about..."
    - Typed object: {"type": "/type/text", "value": "A novel about..."}

    Returns the description string or None if unavailable / request fails.
    """
    # Normalise key: strip leading slash
    key = work_key.lstrip("/")
    if not key.startswith("works/"):
        key = f"works/{key}"

    url = f"{_BASE}/{key}.json"
    try:
        resp = httpx.get(url, timeout=_TIMEOUT)
        if resp.status_code == 404:
            logger.debug("Open Library work not found: %s", url)
            return None
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("Open Library HTTP error for %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("Open Library request failed for %s: %s", url, exc)
        return None

    raw_desc = data.get("description")
    if raw_desc is None:
        return None

    # Handle typed object form
    if isinstance(raw_desc, dict):
        return raw_desc.get("value") or None

    # Handle plain string form
    if isinstance(raw_desc, str):
        return raw_desc.strip() or None

    return None


def get_book_data(title: str, author: str | None = None) -> dict[str, Any] | None:
    """High-level helper: search + fetch description in one call.

    Returns a dict with keys:
        description: str | None
        subjects: list[str]
        first_published_year: int | None
        isbn: str | None
        source: 'open_library'

    Returns None if the book is not found.
    """
    doc = search_book(title, author)
    if doc is None:
        return None

    # Extract description: first try from search result (rare), then fetch work
    description: str | None = None
    work_key = doc.get("key")
    if work_key:
        description = fetch_work_description(work_key)

    # Subjects from search result
    subjects: list[str] = doc.get("subject", []) or []

    # First publish year
    first_published_year: int | None = doc.get("first_publish_year")

    # ISBN: prefer ISBN-13 (13 digits), then ISBN-10
    isbn_list: list[str] = doc.get("isbn", []) or []
    isbn: str | None = None
    for candidate in isbn_list:
        candidate = candidate.strip()
        if len(candidate) == 13:
            isbn = candidate
            break
    if isbn is None:
        for candidate in isbn_list:
            candidate = candidate.strip()
            if len(candidate) == 10:
                isbn = candidate
                break

    return {
        "description": description,
        "subjects": subjects,
        "first_published_year": first_published_year,
        "isbn": isbn,
        "source": "open_library",
    }
