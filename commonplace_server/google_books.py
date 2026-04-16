"""Google Books public API client.

Pure functions; no API key required for basic metadata search (anonymous
quota is 1000 req/day).  Uses httpx for HTTP.

Responses are cached to ~/.cache/commonplace/google_books/<cache_key>.json
so re-enrichment runs don't burn quota.

Primary entry point:
    get_book_data(title, author) -> dict | None
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://www.googleapis.com/books/v1"
_TIMEOUT = 10.0
_CACHE_DIR = Path("~/.cache/commonplace/google_books").expanduser()


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_key(title: str, author: str | None) -> str:
    """Return a filesystem-safe cache key for (title, author)."""
    raw = f"{title}\n{author or ''}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _load_cache(key: str) -> dict[str, Any] | None:
    """Return cached data for *key* or None if absent / corrupt."""
    path = _CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("google_books cache read error for %s: %s", key, exc)
        return None


def _save_cache(key: str, data: dict[str, Any]) -> None:
    """Persist *data* to the cache file for *key*."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / f"{key}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("google_books cache write error for %s: %s", key, exc)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def search_book(title: str, author: str | None = None) -> dict[str, Any] | None:
    """Search Google Books for a volume by title and author.

    Returns the first volumeInfo dict or None on failure / no results.
    """
    if not title:
        return None

    query_parts = [f"intitle:{title}"]
    if author:
        query_parts.append(f"inauthor:{author}")
    query = "+".join(query_parts)

    params = {"q": query, "maxResults": 1, "printType": "books"}

    try:
        resp = httpx.get(f"{_BASE}/volumes", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Google Books search failed for %r: %s", title, exc)
        return None

    items = data.get("items", [])
    if not items:
        logger.debug("Google Books: no results for %r", title)
        return None

    return items[0].get("volumeInfo")


def _extract_isbn(volume_info: dict[str, Any]) -> str | None:
    """Extract the best ISBN from volumeInfo.industryIdentifiers."""
    identifiers = volume_info.get("industryIdentifiers") or []
    isbn13: str | None = None
    isbn10: str | None = None
    for entry in identifiers:
        id_type = entry.get("type", "")
        identifier = entry.get("identifier", "").strip()
        if id_type == "ISBN_13" and not isbn13:
            isbn13 = identifier
        elif id_type == "ISBN_10" and not isbn10:
            isbn10 = identifier
    return isbn13 or isbn10


def _extract_year(volume_info: dict[str, Any]) -> int | None:
    """Extract the publication year from publishedDate (YYYY, YYYY-MM, YYYY-MM-DD)."""
    published = volume_info.get("publishedDate", "")
    if not published:
        return None
    try:
        return int(str(published)[:4])
    except (ValueError, TypeError):
        return None


def get_book_data(title: str, author: str | None = None) -> dict[str, Any] | None:
    """High-level helper: search Google Books and return normalised data.

    Results are cached to avoid burning daily quota on repeated runs.

    Returns a dict with keys:
        description: str | None
        subjects: list[str]
        first_published_year: int | None
        isbn: str | None
        source: 'google_books'

    Returns None if the book is not found.
    """
    if not title:
        return None

    cache_key = _cache_key(title, author)
    cached = _load_cache(cache_key)
    if cached is not None:
        logger.debug("google_books cache hit for %r", title)
        return cached

    volume_info = search_book(title, author)
    if volume_info is None:
        return None

    description: str | None = volume_info.get("description") or None
    subjects: list[str] = volume_info.get("categories") or []
    isbn = _extract_isbn(volume_info)
    first_published_year = _extract_year(volume_info)

    result: dict[str, Any] = {
        "description": description,
        "subjects": subjects,
        "first_published_year": first_published_year,
        "isbn": isbn,
        "source": "google_books",
    }

    _save_cache(cache_key, result)
    return result
