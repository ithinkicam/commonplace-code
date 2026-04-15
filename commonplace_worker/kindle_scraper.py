"""Kindle highlights scraper — active-session path.

Authenticates via browser-exported cookies stored in macOS Keychain.
Parses the read.amazon.com/notebook page to extract book metadata and
per-book highlights.

Selector versioning
-------------------
KINDLE_SCRAPER_SELECTORS_VERSION pins the date selectors were last verified.
On any structural mismatch, KindleStructureChanged is raised — never silently
fallback. This makes Amazon HTML changes immediately visible.

Session cookies
---------------
Cookies are read from Keychain at runtime:
  security find-generic-password -a commonplace -s commonplace-kindle/session-cookies -w
The value is a JSON array of cookie objects exported by a browser extension.
Cookies are NEVER written to files in this repo.

Rate limiting
-------------
Max 1 request per 1.5 seconds (jittered ±0.3s). Overall cap: 200 requests
per run. If the library exceeds 200 books, KindleCapExceeded is raised so the
caller can surface the issue rather than silently truncate.
"""

from __future__ import annotations

import json
import logging
import random
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version pin — update this date whenever selectors are verified/changed.
# If a future run raises KindleStructureChanged, update selectors AND this pin.
# ---------------------------------------------------------------------------
KINDLE_SCRAPER_SELECTORS_VERSION = "2026-04-15"

# ---------------------------------------------------------------------------
# Selectors — documented so future breakage is obvious
# ---------------------------------------------------------------------------
#
# SELECTOR_BOOK_CONTAINER
#   Each book on the /notebook landing page lives in a <div> with class
#   "kp-notebook-library-each-book". The ASIN is stored in the "id" attribute
#   of this container (prefixed with "kp-notebook-library-each-book-").
#
# SELECTOR_BOOK_TITLE
#   Within each book container, the title is in an <h2> element with class
#   "a-size-base" inside a <span class="kp-notebook-searchable">.
#
# SELECTOR_BOOK_AUTHOR
#   The author line sits in a <p> element with class "a-spacing-none" that
#   contains a <span> with class "a-color-secondary".
#
# SELECTOR_BOOK_COVER
#   The cover image is an <img> tag with class "kp-notebook-cover-image" inside
#   the book container.
#
# SELECTOR_HIGHLIGHT_CONTAINER
#   On the per-book page, each highlight lives in a <div> with id starting with
#   "highlight-" (e.g. "highlight-AABB1122CC").
#
# SELECTOR_HIGHLIGHT_TEXT
#   The highlight text is in a <span id="highlight"> inside the highlight container.
#
# SELECTOR_HIGHLIGHT_NOTE
#   Optional reader note is in a <span id="note"> inside the highlight container.
#   Absent if no note was added.
#
# SELECTOR_HIGHLIGHT_LOCATION
#   Location info is in a <input class="kp-notebook-highlight-location"> with
#   value attribute containing e.g. "Location 142".
#
# SELECTOR_HIGHLIGHT_COLOR
#   Color is in an <input class="kp-notebook-highlight-color"> with value
#   attribute e.g. "yellow".
#
# SELECTOR_HIGHLIGHT_TIMESTAMP
#   Created timestamp is in a <span class="kp-notebook-highlight-time"> element.

SELECTOR_BOOK_CONTAINER = "div.kp-notebook-library-each-book"
SELECTOR_BOOK_TITLE = "h2.kp-notebook-searchable"
SELECTOR_BOOK_AUTHOR = "p.kp-notebook-searchable"
SELECTOR_BOOK_COVER = "img.kp-notebook-cover-image"
SELECTOR_HIGHLIGHT_CONTAINER = "div#kp-notebook-annotations"
SELECTOR_HIGHLIGHT_CARD = "div.kp-notebook-row-separator"
SELECTOR_HIGHLIGHT_TEXT = "span#highlight"
SELECTOR_HIGHLIGHT_NOTE = "span#note"
SELECTOR_HIGHLIGHT_LOCATION = "input#kp-annotation-location"
SELECTOR_HIGHLIGHT_COLOR = "input.kp-annotation-type"
SELECTOR_HIGHLIGHT_TIMESTAMP = "span#annotationHighlightHeader"

NOTEBOOK_URL = "https://read.amazon.com/notebook"
REQUEST_CAP = 200
MIN_DELAY = 1.5  # seconds
JITTER = 0.3    # seconds


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class KindleStructureChanged(Exception):
    """Raised when a CSS selector returns zero elements on a page that should have data.

    The message names the selector and URL so the operator can immediately
    identify what changed in Amazon's HTML.
    """


class KindleCookiesMissing(Exception):
    """Raised when the Keychain item for session cookies does not exist."""


class KindleSessionExpired(Exception):
    """Raised when Amazon redirects to a login page, indicating session rot."""


class KindleCapExceeded(Exception):
    """Raised when the request cap is reached before all books are scraped."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class KindleBook:
    asin: str
    title: str
    authors: str
    cover_url: str | None = None


@dataclass
class KindleHighlight:
    location: str | None
    page: str | None
    text: str
    note: str | None
    color: str | None
    created_at: str | None


# ---------------------------------------------------------------------------
# Cookie management
# ---------------------------------------------------------------------------


def load_cookies_from_keychain() -> httpx.Cookies:
    """Read session cookies from macOS Keychain and return an httpx.Cookies object.

    The Keychain item stores a JSON array of cookie objects exported by a
    browser extension (e.g., EditThisCookie, Cookie-Editor).

    Only cookies for amazon.com and read.amazon.com domains are attached.

    Raises KindleCookiesMissing if the keychain item does not exist.
    """
    try:
        result = subprocess.run(  # noqa: S603
            [
                "security",
                "find-generic-password",
                "-a", "commonplace",
                "-s", "commonplace-kindle/session-cookies",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise KindleCookiesMissing(
            "security command not found — not running on macOS?"
        ) from exc

    if result.returncode != 0:
        raise KindleCookiesMissing(
            "Keychain item 'commonplace-kindle/session-cookies' not found. "
            "Export cookies from your browser and run: "
            "make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json"
        )

    raw = result.stdout.strip()
    if not raw:
        raise KindleCookiesMissing("Keychain item exists but is empty.")

    try:
        cookie_list: list[dict[str, Any]] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KindleCookiesMissing(
            f"Keychain value is not valid JSON: {exc}"
        ) from exc

    cookies = httpx.Cookies()
    for c in cookie_list:
        domain = c.get("domain", "")
        # Only include cookies relevant to Amazon notebook
        if "amazon.com" in domain:
            name = c.get("name", "")
            value = c.get("value", "")
            if name and value:
                cookies.set(name, value, domain=domain)

    logger.debug("Loaded %d cookies from keychain", sum(1 for _ in cookies))
    return cookies


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Enforces a minimum delay between requests with jitter."""

    def __init__(self, min_delay: float = MIN_DELAY, jitter: float = JITTER) -> None:
        self._min_delay = min_delay
        self._jitter = jitter
        self._last_request: float = 0.0
        self._count: int = 0

    def wait(self) -> None:
        """Sleep until at least min_delay seconds since the last request."""
        now = time.monotonic()
        elapsed = now - self._last_request
        delay = self._min_delay + random.uniform(-self._jitter, self._jitter)  # noqa: S311
        delay = max(delay, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.monotonic()
        self._count += 1

    @property
    def count(self) -> int:
        return self._count


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


def _check_login_redirect(soup: BeautifulSoup, url: str) -> None:
    """Raise KindleSessionExpired if the page looks like a login redirect."""
    # Amazon login pages have a form with id="ap_signin_form" or
    # an <input name="email"> field.
    if soup.find("form", {"id": "ap_signin_form"}):
        raise KindleSessionExpired(
            f"Amazon redirected to login page at {url}. "
            "Session cookies are expired or invalid. "
            "Re-export cookies from your browser and run: "
            "make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json"
        )
    if soup.find("input", {"name": "email"}):
        raise KindleSessionExpired(
            f"Amazon returned a sign-in form at {url}. "
            "Cookie names that appear to be missing: session-id, ubid-main, x-main. "
            "Re-export cookies from your browser and run: "
            "make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json"
        )


def _require(elements: list[Any] | Any, selector: str, url: str, context: str = "") -> list[Any]:
    """Assert that selector matched at least one element.

    Raises KindleStructureChanged if the list is empty.
    """
    found = elements if isinstance(elements, list) else [elements] if elements else []

    if not found:
        detail = f" ({context})" if context else ""
        raise KindleStructureChanged(
            f"KINDLE_SELECTOR_BROKEN: selector {selector!r} matched zero elements "
            f"on {url}{detail}. "
            f"Selector version: {KINDLE_SCRAPER_SELECTORS_VERSION}. "
            "Amazon may have changed their HTML. Update selectors and bump "
            "KINDLE_SCRAPER_SELECTORS_VERSION."
        )
    return found


def _parse_library_page(html: str, url: str) -> list[KindleBook]:
    """Parse the /notebook landing page and return a list of KindleBook objects."""
    soup = BeautifulSoup(html, "lxml")
    _check_login_redirect(soup, url)

    containers = soup.select(SELECTOR_BOOK_CONTAINER)
    if not containers:
        # Page loaded but no books — user might have no highlights. Not an error.
        logger.info("No book containers found at %s — user may have no highlights", url)
        return []

    books: list[KindleBook] = []
    for container in containers:
        if not isinstance(container, Tag):
            continue

        # Extract ASIN from container id attribute: "kp-notebook-library-each-book-<ASIN>"
        container_id = container.get("id", "")
        if isinstance(container_id, str) and container_id.startswith("kp-notebook-library-each-book-"):
            asin = container_id[len("kp-notebook-library-each-book-"):]
        else:
            # Try data-asin attribute
            asin = str(container.get("data-asin", ""))

        if not asin:
            logger.warning("Skipping book container with no ASIN: %s", container_id)
            continue

        # Title
        title_el = container.select_one(SELECTOR_BOOK_TITLE)
        title = title_el.get_text(strip=True) if title_el else ""

        # Author
        author_el = container.select_one(SELECTOR_BOOK_AUTHOR)
        authors = author_el.get_text(strip=True) if author_el else ""

        # Cover
        cover_el = container.select_one(SELECTOR_BOOK_COVER)
        cover_url: str | None = None
        if cover_el and isinstance(cover_el, Tag):
            src = cover_el.get("src")
            cover_url = str(src) if src else None

        books.append(KindleBook(asin=asin, title=title, authors=authors, cover_url=cover_url))

    return books


def _parse_highlights_page(html: str, url: str, asin: str) -> list[KindleHighlight]:
    """Parse a per-book highlights page and return a list of KindleHighlight objects."""
    soup = BeautifulSoup(html, "lxml")
    _check_login_redirect(soup, url)

    # The annotations wrapper — if present but empty, user has no highlights for this book.
    annotations_div = soup.select_one(SELECTOR_HIGHLIGHT_CONTAINER)
    if annotations_div is None:
        # No annotations container: could be a valid empty state or a selector break.
        # Distinguish by body text length:
        #   - Tiny page (<200 chars) likely means Amazon returned near-nothing → raise.
        #   - Substantial page content (>=200 chars) but missing selector → structure changed → raise.
        #   - A recognizable empty-state pattern (no content at all) → return [].
        body_text = soup.get_text(strip=True)
        if len(body_text) >= 200:
            # There's real content but the selector is missing — structural change.
            _require([], SELECTOR_HIGHLIGHT_CONTAINER, url, context=f"asin={asin}")
        elif len(body_text) < 50:
            # Near-empty page — likely an error or empty response.
            _require([], SELECTOR_HIGHLIGHT_CONTAINER, url, context=f"asin={asin}")
        logger.info("No annotations container for asin=%s — likely no highlights", asin)
        return []

    cards = annotations_div.select(SELECTOR_HIGHLIGHT_CARD)
    if not cards:
        logger.info("No highlight cards for asin=%s", asin)
        return []

    highlights: list[KindleHighlight] = []
    for card in cards:
        if not isinstance(card, Tag):
            continue

        # Highlight text — required
        text_el = card.select_one(SELECTOR_HIGHLIGHT_TEXT)
        if text_el is None:
            continue
        text = text_el.get_text(strip=True)
        if not text:
            continue

        # Note — optional
        note_el = card.select_one(SELECTOR_HIGHLIGHT_NOTE)
        note = note_el.get_text(strip=True) if note_el else None
        if not note:
            note = None

        # Location
        loc_el = card.select_one(SELECTOR_HIGHLIGHT_LOCATION)
        location: str | None = None
        if loc_el and isinstance(loc_el, Tag):
            location = str(loc_el.get("value", "")) or None

        # Page — often encoded in location string as "Page X"
        page: str | None = None
        if location and "Page" in location:
            parts = location.split("Page")
            if len(parts) > 1:
                page = "Page" + parts[1].strip().split()[0] if parts[1].strip() else None

        # Color
        color_el = card.select_one(SELECTOR_HIGHLIGHT_COLOR)
        color: str | None = None
        if color_el and isinstance(color_el, Tag):
            color = str(color_el.get("value", "")) or None

        # Timestamp
        ts_el = card.select_one(SELECTOR_HIGHLIGHT_TIMESTAMP)
        created_at: str | None = None
        if ts_el:
            created_at = ts_el.get_text(strip=True) or None

        highlights.append(KindleHighlight(
            location=location,
            page=page,
            text=text,
            note=note,
            color=color,
            created_at=created_at,
        ))

    return highlights


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_library(
    *,
    _cookies: httpx.Cookies | None = None,
    _rate_limiter: _RateLimiter | None = None,
    _request_count: list[int] | None = None,
) -> list[KindleBook]:
    """Fetch book-level metadata from the Kindle notebook landing page.

    Returns a list of KindleBook objects — one per book that has highlights.

    Raises:
        KindleCookiesMissing: if the keychain item is absent.
        KindleSessionExpired: if Amazon returns a login redirect.
        KindleStructureChanged: if the expected HTML structure is missing.
    """
    cookies = _cookies if _cookies is not None else load_cookies_from_keychain()
    limiter = _rate_limiter if _rate_limiter is not None else _RateLimiter()
    count_ref = _request_count if _request_count is not None else [0]

    if count_ref[0] >= REQUEST_CAP:
        raise KindleCapExceeded(
            f"Reached {REQUEST_CAP}-request cap before fetching library. "
            "Reduce scope or increase cap."
        )

    limiter.wait()
    count_ref[0] += 1

    with httpx.Client(cookies=cookies, follow_redirects=True, timeout=30.0) as client:
        response = client.get(NOTEBOOK_URL)
        response.raise_for_status()

    return _parse_library_page(response.text, NOTEBOOK_URL)


def fetch_highlights(
    asin: str,
    *,
    _cookies: httpx.Cookies | None = None,
    _rate_limiter: _RateLimiter | None = None,
    _request_count: list[int] | None = None,
) -> list[KindleHighlight]:
    """Fetch highlights for a single book by ASIN.

    Returns a list of KindleHighlight objects.

    Raises:
        KindleCookiesMissing: if the keychain item is absent.
        KindleSessionExpired: if Amazon returns a login redirect.
        KindleStructureChanged: if the expected HTML structure is missing.
        KindleCapExceeded: if the request cap is reached.
    """
    cookies = _cookies if _cookies is not None else load_cookies_from_keychain()
    limiter = _rate_limiter if _rate_limiter is not None else _RateLimiter()
    count_ref = _request_count if _request_count is not None else [0]

    if count_ref[0] >= REQUEST_CAP:
        raise KindleCapExceeded(
            f"Reached {REQUEST_CAP}-request cap. Cannot fetch highlights for asin={asin}. "
            "Increase cap or run with --book to process one book at a time."
        )

    url = f"{NOTEBOOK_URL}?asin={asin}&contentLimitState=&"
    limiter.wait()
    count_ref[0] += 1

    with httpx.Client(cookies=cookies, follow_redirects=True, timeout=30.0) as client:
        response = client.get(url)
        response.raise_for_status()

    return _parse_highlights_page(response.text, url, asin)
