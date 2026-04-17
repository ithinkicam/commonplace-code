"""Tests for scripts/bcp_crawler.py.

Uses httpx.MockTransport (no network I/O).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Load module under test
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _load_crawler():
    spec = importlib.util.spec_from_file_location(
        "bcp_crawler", _SCRIPTS_DIR / "bcp_crawler.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


crawler = _load_crawler()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOT = "https://www.bcponline.org/"


def _make_html(*links: str) -> bytes:
    hrefs = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return f"<html><body>{hrefs}</body></html>".encode()


def _make_client(routes: dict[str, tuple[int, bytes]]) -> httpx.Client:
    """Build an httpx.Client backed by a MockTransport from a URL→(status, body) map."""

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Exact match first, then prefix match for redirects
        if url in routes:
            status, body = routes[url]
            return httpx.Response(status, content=body)
        # Default 404
        return httpx.Response(404, content=b"not found")

    transport = httpx.MockTransport(_handler)
    return httpx.Client(transport=transport, headers={"User-Agent": "test"})


# ---------------------------------------------------------------------------
# 1. URL-to-path mapping
# ---------------------------------------------------------------------------


class TestUrlToPath:
    def test_root_maps_to_index(self, tmp_path: Path) -> None:
        p = crawler.url_to_cache_path(_ROOT, tmp_path)
        assert p == (tmp_path / "www.bcponline.org" / "index.html").resolve()

    def test_simple_path(self, tmp_path: Path) -> None:
        url = "https://www.bcponline.org/DailyOffice/mp.html"
        p = crawler.url_to_cache_path(url, tmp_path)
        assert p == (tmp_path / "www.bcponline.org" / "DailyOffice" / "mp.html").resolve()

    def test_trailing_slash_directory(self, tmp_path: Path) -> None:
        url = "https://www.bcponline.org/Psalter/"
        p = crawler.url_to_cache_path(url, tmp_path)
        # Last non-empty segment is "Psalter", gets .html
        assert p.name == "Psalter.html"

    def test_query_string_produces_hash_suffix(self, tmp_path: Path) -> None:
        url_qs = "https://www.bcponline.org/page.html?section=2"
        url_no_qs = "https://www.bcponline.org/page.html"
        p_qs = crawler.url_to_cache_path(url_qs, tmp_path)
        p_no = crawler.url_to_cache_path(url_no_qs, tmp_path)
        # Should differ
        assert p_qs != p_no
        # QS path should contain __q
        assert "__q" in p_qs.name

    def test_query_deterministic(self, tmp_path: Path) -> None:
        url = "https://www.bcponline.org/foo.html?a=1&b=2"
        assert crawler.url_to_cache_path(url, tmp_path) == crawler.url_to_cache_path(url, tmp_path)

    def test_path_traversal_dot_dot_sanitised(self, tmp_path: Path) -> None:
        url = "https://www.bcponline.org/foo/../../../etc/passwd"
        # Dots are replaced, result stays inside cache_dir
        p = crawler.url_to_cache_path(url, tmp_path)
        assert str(p).startswith(str(tmp_path.resolve()))

    def test_path_traversal_encoded_dot_dot(self, tmp_path: Path) -> None:
        url = "https://www.bcponline.org/%2e%2e/%2e%2e/etc/passwd"
        p = crawler.url_to_cache_path(url, tmp_path)
        assert str(p).startswith(str(tmp_path.resolve()))


# ---------------------------------------------------------------------------
# 2. Atomic write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_final_file_present_after_write(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "page.html"
        crawler.atomic_write(target, b"hello")
        assert target.read_bytes() == b"hello"

    def test_tmp_file_does_not_persist(self, tmp_path: Path) -> None:
        target = tmp_path / "page.html"
        crawler.atomic_write(target, b"content")
        tmp = target.with_suffix(".html.tmp")
        assert not tmp.exists()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "page.html"
        crawler.atomic_write(deep, b"x")
        assert deep.exists()


# ---------------------------------------------------------------------------
# 3. Resumability
# ---------------------------------------------------------------------------


class TestResumability:
    def test_cached_page_not_refetched(self, tmp_path: Path) -> None:
        """If the cache file already exists, the crawler skips the request."""
        cache_path = crawler.url_to_cache_path(_ROOT, tmp_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(_make_html())

        fetched_urls: list[str] = []

        def _handler(req: httpx.Request) -> httpx.Response:
            fetched_urls.append(str(req.url))
            return httpx.Response(200, content=_make_html())

        client = httpx.Client(transport=httpx.MockTransport(_handler))
        with client:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=10,
                crawl_delay=0,
                dry_run=False,
                client=client,
            )
        assert rc == 0
        assert _ROOT not in fetched_urls, "Should have skipped cached root"

    def test_second_run_no_new_writes(self, tmp_path: Path) -> None:
        """Two runs produce the same cache with no extra fetches on the second."""
        html = _make_html()

        def _handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=html)

        # First run
        with _make_client({_ROOT: (200, html)}) as c:
            crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )

        files_after_first = list(tmp_path.rglob("*.html"))

        fetched_on_second: list[str] = []

        def _handler2(req: httpx.Request) -> httpx.Response:
            fetched_on_second.append(str(req.url))
            return httpx.Response(200, content=html)

        with httpx.Client(transport=httpx.MockTransport(_handler2)) as c2:
            crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=False,
                client=c2,
            )

        assert len(fetched_on_second) == 0, "Second run should fetch nothing new"
        assert list(tmp_path.rglob("*.html")) == files_after_first


# ---------------------------------------------------------------------------
# 4. HTTP error handling
# ---------------------------------------------------------------------------


class TestHttpErrors:
    def test_404_logged_and_skipped(self, tmp_path: Path) -> None:
        with _make_client({_ROOT: (404, b"not found")}) as c:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )
        assert rc == 0
        # No file written
        assert not list(tmp_path.rglob("*.html"))

    def test_500_logged_and_skipped(self, tmp_path: Path) -> None:
        with _make_client({_ROOT: (500, b"error")}) as c:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )
        assert rc == 0
        assert not list(tmp_path.rglob("*.html"))

    def test_429_bails_with_nonzero_exit(self, tmp_path: Path) -> None:
        with _make_client({_ROOT: (429, b"slow down")}) as c:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )
        assert rc == 1

    def test_request_error_continues(self, tmp_path: Path) -> None:
        """A network error on one URL shouldn't abort the whole crawl."""
        # Root returns a link to /page.html; root itself raises a connection error.
        call_count = 0

        def _handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, content=_make_html())

        client = httpx.Client(transport=httpx.MockTransport(_handler))
        with client:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=False,
                client=client,
            )
        assert rc == 0


# ---------------------------------------------------------------------------
# 5. Scope filtering
# ---------------------------------------------------------------------------


class TestScopeFiltering:
    def test_off_host_url_rejected(self) -> None:
        assert not crawler.is_in_scope("https://example.com/foo")

    def test_on_host_url_accepted(self) -> None:
        assert crawler.is_in_scope("https://www.bcponline.org/foo")

    def test_mailto_not_extracted(self) -> None:
        html = b'<html><body><a href="mailto:foo@bar.com">email</a></body></html>'
        links = crawler.extract_links(html.decode(), _ROOT)
        assert not any("mailto" in lnk for lnk in links)

    def test_fragment_only_not_extracted(self) -> None:
        html = b'<html><body><a href="#section2">anchor</a></body></html>'
        links = crawler.extract_links(html.decode(), _ROOT)
        assert links == []

    def test_off_site_link_not_queued(self, tmp_path: Path) -> None:
        html = _make_html("https://external.com/page")
        with _make_client({_ROOT: (200, html)}) as c:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=10,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )
        assert rc == 0
        # Only root should be in cache
        cached = list(tmp_path.rglob("*.html"))
        assert len(cached) == 1

    def test_fragment_stripped_from_discovered_link(self) -> None:
        html = '<html><body><a href="/foo.html#section">link</a></body></html>'
        links = crawler.extract_links(html, _ROOT)
        assert links == ["https://www.bcponline.org/foo.html"]


# ---------------------------------------------------------------------------
# 6. Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_writes_no_files(self, tmp_path: Path) -> None:
        html = _make_html("/DailyOffice/mp.html", "/Psalter/index.html")

        def _handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=html)

        client = httpx.Client(transport=httpx.MockTransport(_handler))
        with client:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=10,
                crawl_delay=0,
                dry_run=True,
                client=client,
            )
        assert rc == 0
        assert list(tmp_path.rglob("*.html")) == [], "dry-run must not write any files"

    def test_dry_run_does_not_request_network(self, tmp_path: Path) -> None:
        requested: list[str] = []

        def _handler(req: httpx.Request) -> httpx.Response:
            requested.append(str(req.url))
            return httpx.Response(200, content=_make_html())

        client = httpx.Client(transport=httpx.MockTransport(_handler))
        with client:
            crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=True,
                client=client,
            )
        # In dry-run, the root is not actually fetched (no network call)
        assert requested == []


# ---------------------------------------------------------------------------
# 7. Successful crawl — happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_single_page_written(self, tmp_path: Path) -> None:
        with _make_client({_ROOT: (200, _make_html())}) as c:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=5,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )
        assert rc == 0
        cached = list(tmp_path.rglob("*.html"))
        assert len(cached) == 1

    def test_links_followed(self, tmp_path: Path) -> None:
        sub_url = "https://www.bcponline.org/sub/page.html"
        routes = {
            _ROOT: (200, _make_html(sub_url)),
            sub_url: (200, _make_html()),
        }
        with _make_client(routes) as c:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=10,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )
        assert rc == 0
        cached = list(tmp_path.rglob("*.html"))
        assert len(cached) == 2

    def test_max_pages_cap_respected(self, tmp_path: Path) -> None:
        # Root links to 10 pages; cap at 3 total
        links = [f"https://www.bcponline.org/p{i}.html" for i in range(10)]
        routes: dict[str, tuple[int, bytes]] = {_ROOT: (200, _make_html(*links))}
        for link in links:
            routes[link] = (200, _make_html())
        with _make_client(routes) as c:
            rc = crawler.crawl(
                start_url=_ROOT,
                cache_dir=tmp_path,
                max_pages=3,
                crawl_delay=0,
                dry_run=False,
                client=c,
            )
        assert rc == 0
        cached = list(tmp_path.rglob("*.html"))
        assert len(cached) <= 3
