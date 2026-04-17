#!/usr/bin/env python3
"""BCP 1979 crawler — polite, resumable HTML cache builder for bcponline.org.

Crawl budget and politeness policy
------------------------------------
- Crawl-delay: 180 seconds (from robots.txt at bcponline.org). The default
  ``--crawl-delay`` is set to 180 and **must not** be lowered in production.
- User-Agent: ``Commonplace/0.1 (personal archive; contact: camlewis35@gmail.com)``
- A 429 response causes an immediate non-zero exit — the server is telling us to
  back off entirely. All other 4xx/5xx are logged and skipped.
- Scope is limited to the ``www.bcponline.org`` host. Mailto links, fragment-only
  anchors, and off-site URLs are silently dropped from the crawl queue.

URL → cache path mapping
--------------------------
The mapping strips the scheme, keeps the host as a top-level directory, then
maps the URL path to a filesystem path with ``.html`` appended.

Rules (in order):
1. Scheme and netloc are normalised to lower-case.
2. The URL path is split on ``/``. Empty segments (from leading/trailing slashes
   or doubled slashes) are removed.
3. Each segment is percent-decoded, then any character that is not alphanumeric,
   hyphen, underscore, or period is replaced with ``_``. Segments that are only
   dots (``..``, ``.``) are replaced with ``__dot__`` to prevent path traversal.
4. If the resulting path list is empty, the file is named ``index.html`` directly
   under the host directory. Otherwise the last segment gets ``.html`` appended
   and the rest form intermediate directories.
5. Query strings: the query (``?foo=bar``) is serialised as a sorted, URL-safe
   string, hashed to 8 hex chars with SHA-256, and appended to the filename
   before ``.html`` as ``__q<hash>``. This avoids filesystem collisions between
   URLs that differ only in query params.
6. All paths are resolved through ``Path.resolve()`` and verified to remain
   inside ``cache_dir`` — if a constructed path would escape the cache root
   (e.g. via a malformed URL) the URL is skipped with a warning.

Example::
    https://www.bcponline.org/DailyOffice/mp.html
    → <cache_dir>/www.bcponline.org/DailyOffice/mp.html

    https://www.bcponline.org/
    → <cache_dir>/www.bcponline.org/index.html

Atomic writes
--------------
Each file is written to ``<target>.html.tmp``, fsynced, then renamed over the
final path. A crash mid-write therefore leaves a ``.tmp`` orphan that will be
overwritten on the next run, not a silently corrupt ``.html`` file.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
import urllib.parse
from collections import deque
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

DEFAULT_START_URL = "https://www.bcponline.org/"
DEFAULT_CACHE_DIR = Path("~/commonplace/cache/bcp_1979")
DEFAULT_MAX_PAGES = 300
DEFAULT_CRAWL_DELAY = 180
USER_AGENT = "Commonplace/0.1 (personal archive; contact: camlewis35@gmail.com)"
TARGET_HOST = "www.bcponline.org"

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL → path mapping
# ---------------------------------------------------------------------------

_SAFE_SEGMENT_RE = __import__("re").compile(r"[^a-zA-Z0-9\-_.]")
_DOT_ONLY_RE = __import__("re").compile(r"^\.+$")


def url_to_cache_path(url: str, cache_dir: Path) -> Path:
    """Return the deterministic cache path for *url* under *cache_dir*.

    Raises ``ValueError`` if the resulting path would escape *cache_dir*.
    """
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()

    # Split path into non-empty segments
    raw_segments = [s for s in parsed.path.split("/") if s]

    # Sanitise each segment
    clean_segments: list[str] = []
    for seg in raw_segments:
        decoded = urllib.parse.unquote(seg)
        if _DOT_ONLY_RE.match(decoded):
            decoded = "__dot__"
        clean = _SAFE_SEGMENT_RE.sub("_", decoded)
        clean_segments.append(clean)

    # Query-string suffix
    qs_suffix = ""
    if parsed.query:
        qs_hash = hashlib.sha256(parsed.query.encode()).hexdigest()[:8]
        qs_suffix = f"__q{qs_hash}"

    # Build path
    if not clean_segments:
        # Root URL → index.html
        rel = Path(host) / f"index{qs_suffix}.html"
    else:
        # Last segment becomes the filename; rest are directories
        *dirs, filename = clean_segments
        # Don't double-append .html if the segment already ends with it
        if filename.lower().endswith(".html") or filename.lower().endswith(".htm"):
            filename_with_ext = f"{filename}{qs_suffix}"
        else:
            filename_with_ext = f"{filename}{qs_suffix}.html"
        rel = Path(host, *dirs, filename_with_ext)

    target = (cache_dir / rel).resolve()

    # Safety: ensure we stay inside cache_dir
    resolved_root = cache_dir.resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"URL {url!r} maps outside cache_dir {cache_dir}: {target}"
        ) from exc

    return target


def cache_path_to_url_hint(path: Path, cache_dir: Path) -> str:
    """Return a best-effort URL from a cache path (for logging only)."""
    rel = path.relative_to(cache_dir.resolve())
    parts = list(rel.parts)
    if not parts:
        return ""
    host = parts[0]
    rest = "/".join(parts[1:])
    return f"https://{host}/{rest}"


# ---------------------------------------------------------------------------
# Link discovery
# ---------------------------------------------------------------------------


def extract_links(html: str, base_url: str) -> list[str]:
    """Return absolute in-scope URLs found in *html*.

    Follows `<a href>` plus `<frame src>` and `<iframe src>` — bcponline.org
    is a frameset site (nav.html + title.html) with zero anchors on the
    landing page, so missing frames would leave the queue empty.
    """
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    candidates: list[str] = []
    for tag in soup.find_all("a", href=True):
        candidates.append(str(tag["href"]).strip())
    for tag in soup.find_all(["frame", "iframe"], src=True):
        candidates.append(str(tag["src"]).strip())
    for raw in candidates:
        if not raw or raw.startswith("#") or raw.lower().startswith("mailto:"):
            continue
        absolute = urllib.parse.urljoin(base_url, raw)
        parsed = urllib.parse.urlparse(absolute)
        absolute = urllib.parse.urlunparse(parsed._replace(fragment=""))
        if parsed.scheme not in ("http", "https"):
            continue
        links.append(absolute)
    return links


def is_in_scope(url: str) -> bool:
    """Return True if *url* is on the target host."""
    parsed = urllib.parse.urlparse(url)
    return (parsed.hostname or "").lower() == TARGET_HOST


# ---------------------------------------------------------------------------
# Atomic file write
# ---------------------------------------------------------------------------


def atomic_write(path: Path, content: bytes) -> None:
    """Write *content* to *path* atomically (tmp → fsync → rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.rename(path)


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------


def crawl(
    *,
    start_url: str,
    cache_dir: Path,
    max_pages: int,
    crawl_delay: float,
    dry_run: bool,
    client: httpx.Client,
) -> int:
    """Run the crawl. Returns exit code (0 = success, 1 = 429 bail-out)."""
    cache_dir = cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    queue: deque[str] = deque()

    def _normalise(u: str) -> str:
        p = urllib.parse.urlparse(u)
        # Drop fragment; normalise scheme+host to lower
        return urllib.parse.urlunparse(
            p._replace(scheme=p.scheme.lower(), netloc=(p.netloc or "").lower(), fragment="")
        )

    start_url = _normalise(start_url)
    queue.append(start_url)
    seen.add(start_url)

    fetched = 0
    skipped_cached = 0

    while queue and fetched + skipped_cached < max_pages:
        url = queue.popleft()
        total_so_far = fetched + skipped_cached + 1

        try:
            cache_path = url_to_cache_path(url, cache_dir)
        except ValueError as exc:
            logger.warning("Skipping unsafe URL %r: %s", url, exc)
            continue

        # Resumability: skip already-cached pages
        if cache_path.exists():
            logger.info("[%d] SKIP %s (cached)", total_so_far, url)
            skipped_cached += 1
            # Still parse cached HTML to discover new links
            html_text = cache_path.read_text(errors="replace")
            for link in extract_links(html_text, url):
                norm = _normalise(link)
                if norm not in seen and is_in_scope(norm):
                    seen.add(norm)
                    queue.append(norm)
            continue

        if dry_run:
            logger.info("[%d] DRY-RUN %s → would fetch → %s", total_so_far, url, cache_path)
            fetched += 1
            continue

        # Fetch
        try:
            resp = client.get(url, follow_redirects=True)
        except httpx.RequestError as exc:
            logger.warning("[%d] ERROR %s: %s", total_so_far, url, exc)
            fetched += 1
            continue

        if resp.status_code == 429:
            logger.error(
                "[%d] 429 Too Many Requests for %s — backing off immediately. "
                "Increase --crawl-delay or wait before resuming.",
                total_so_far,
                url,
            )
            return 1

        if resp.status_code >= 400:
            logger.warning("[%d] %d %s — skipping", total_so_far, resp.status_code, url)
            fetched += 1
            continue

        # Write atomically
        atomic_write(cache_path, resp.content)
        logger.info("[%d] GET %s → %d, cached at %s", total_so_far, url, resp.status_code, cache_path)
        fetched += 1

        # Discover links
        content_type = resp.headers.get("content-type", "")
        if "html" in content_type or not content_type:
            html_text = resp.text
            for link in extract_links(html_text, str(resp.url)):
                norm = _normalise(link)
                if norm not in seen and is_in_scope(norm):
                    seen.add(norm)
                    queue.append(norm)

        if queue and not dry_run:
            time.sleep(crawl_delay)

    logger.info(
        "Crawl complete. fetched=%d skipped_cached=%d queue_remaining=%d",
        fetched,
        skipped_cached,
        len(queue),
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resumable HTML cache crawler for www.bcponline.org (BCP 1979)."
    )
    p.add_argument(
        "--start-url",
        default=DEFAULT_START_URL,
        help=f"Seed URL (default: {DEFAULT_START_URL})",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Directory for cached HTML files (default: {DEFAULT_CACHE_DIR})",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"Safety cap on total pages visited (default: {DEFAULT_MAX_PAGES})",
    )
    p.add_argument(
        "--crawl-delay",
        type=float,
        default=DEFAULT_CRAWL_DELAY,
        help=f"Seconds to sleep between requests (default: {DEFAULT_CRAWL_DELAY} per robots.txt)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover URLs without fetching or writing files",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    with client:
        return crawl(
            start_url=args.start_url,
            cache_dir=args.cache_dir,
            max_pages=args.max_pages,
            crawl_delay=args.crawl_delay,
            dry_run=args.dry_run,
            client=client,
        )


if __name__ == "__main__":
    sys.exit(main())
