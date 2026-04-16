"""Article URL ingest handler.

``handle_article_ingest(payload, conn)`` is the worker handler for
``ingest_article`` jobs — the share-sheet → ``/capture`` path where a
user taps "share" on a news article or blog post.

Behaviour
---------
1. Validate the payload ``url`` (HTTP/HTTPS only).
2. Fetch the URL (Trafilatura's built-in fetcher, overridable for tests).
3. Extract clean reader-mode markdown + metadata via Trafilatura.
4. Idempotency: dedupe by content hash (SHA-256 of extracted body) and by
   canonical URL (``source_id``). If either matches, return the existing
   ``document_id`` without re-embedding.
5. Write the vault file atomically to
   ``~/commonplace/captures/YYYY/MM/<utc-timestamp>-<slug>.md`` with YAML
   frontmatter.
6. Insert a ``documents`` row (``content_type='article'``) and run
   ``pipeline.embed_document`` to chunk + embed.

Returned dict: ``{document_id, chunk_count, elapsed_ms, url, title}``.

Typed exceptions
----------------
- :class:`ArticleFetchError` — network failure, non-HTTP scheme, non-200.
- :class:`ArticleExtractionError` — Trafilatura could not extract meaningful
  reader-mode body (empty content, paywall, JS-only rendering).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# Minimum characters of extracted body to count as a successful extraction.
# Trafilatura will happily return a few words from a paywall div; require
# a reasonable floor so we surface paywalls as extraction errors.
_MIN_BODY_CHARS = 200


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class ArticleError(Exception):
    """Base class for article handler errors."""


class ArticleFetchError(ArticleError):
    """Fetching the URL failed (network, timeout, non-200, bad scheme)."""


class ArticleExtractionError(ArticleError):
    """Trafilatura returned no usable reader-mode body (paywall / JS page)."""


# ---------------------------------------------------------------------------
# Fetcher type alias
# ---------------------------------------------------------------------------

# A fetcher takes a URL and returns raw HTML text (or raises ArticleFetchError).
FetchFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_article_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _fetcher: FetchFn | None = None,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for ``ingest_article`` jobs.

    Parameters
    ----------
    payload:
        ``{"url": str, "inbox_file": str | None}`` — ``inbox_file`` is the
        optional name of the original ``/capture`` inbox record and is used
        only for logging/provenance.
    conn:
        Open SQLite connection with migrations applied.
    _fetcher:
        Optional ``url -> html`` override for tests.  Leave ``None`` in
        production; Trafilatura's built-in fetcher is used.
    _embedder:
        Optional embedder override forwarded to ``pipeline.embed_document``.

    Returns
    -------
    dict with keys: ``document_id``, ``chunk_count``, ``elapsed_ms``,
    ``url`` (canonical), ``title``.
    """
    t0 = time.monotonic()

    url_raw = payload.get("url")
    if not isinstance(url_raw, str) or not url_raw.strip():
        raise ValueError(f"ingest_article payload missing 'url': {payload!r}")

    canonical_url = _canonicalize_url(url_raw.strip())

    # Fetch
    fetcher: FetchFn = _fetcher if _fetcher is not None else _default_fetcher
    html = fetcher(canonical_url)
    if not html:
        raise ArticleFetchError(f"Empty response body for {canonical_url!r}")

    # Extract
    body_md, meta = _extract(html, canonical_url)
    if not body_md or len(body_md.strip()) < _MIN_BODY_CHARS:
        raise ArticleExtractionError(
            f"Trafilatura returned no usable reader-mode body for {canonical_url!r} "
            f"(got {len(body_md.strip()) if body_md else 0} chars; paywall or JS-only page?)"
        )

    title: str | None = meta.get("title") or None
    author: str | None = meta.get("author") or None
    byline_date: str | None = meta.get("date") or None
    hostname: str | None = meta.get("hostname") or _hostname(canonical_url)

    content_hash = hashlib.sha256(body_md.encode("utf-8")).hexdigest()

    # Idempotency by (content_type, source_id=canonical_url)
    existing = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'article' AND source_id = ?",
        (canonical_url,),
    ).fetchone()
    if existing is None:
        # Fall-back idempotency by content_hash (same article via different URL).
        existing = conn.execute(
            "SELECT id FROM documents WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()

    if existing is not None:
        existing_id = int(existing["id"])
        chunk_count_row = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
        ).fetchone()
        chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "article already ingested document_id=%d url=%s", existing_id, canonical_url
        )
        return {
            "document_id": existing_id,
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "url": canonical_url,
            "title": title,
        }

    # Write vault file atomically.
    fetched_at = datetime.now(UTC)
    vault_path = _write_vault_file(
        canonical_url=canonical_url,
        title=title,
        author=author,
        byline_date=byline_date,
        hostname=hostname,
        body_md=body_md,
        fetched_at=fetched_at,
    )

    # Insert documents row.
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, author, content_hash,
                 raw_path, source_id, status)
            VALUES ('article', ?, ?, ?, ?, ?, ?, 'ingesting')
            """,
            (
                canonical_url,
                title,
                author,
                content_hash,
                str(vault_path),
                canonical_url,
            ),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]

    # Chunk + embed via pipeline.
    from commonplace_server.pipeline import embed_document

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    result = embed_document(document_id, body_md, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested article document_id=%d chunks=%d url=%s elapsed_ms=%.0f",
        document_id,
        result.chunk_count,
        canonical_url,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "url": canonical_url,
        "title": title,
    }


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _canonicalize_url(url: str) -> str:
    """Normalize a URL: http(s) only, drop fragment, strip trailing slash on path.

    Raises ``ArticleFetchError`` if the scheme is not http/https.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ArticleFetchError(
            f"unsupported URL scheme {parsed.scheme!r}: {url!r} "
            "(only http/https are accepted)"
        )
    if not parsed.netloc:
        raise ArticleFetchError(f"URL missing hostname: {url!r}")

    # Drop fragment; keep query (it can change the article).
    path = parsed.path or "/"
    cleaned = parsed._replace(fragment="", path=path)
    return urlunparse(cleaned)


def _hostname(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _default_fetcher(url: str) -> str:
    """Fetch a URL using Trafilatura's built-in downloader.

    Trafilatura handles user-agent rotation, timeouts, and common SSL quirks.
    We wrap network errors in :class:`ArticleFetchError` so the worker sees
    a stable exception type.
    """
    try:
        import trafilatura
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("trafilatura is not installed") from exc

    try:
        html = trafilatura.fetch_url(url)
    except Exception as exc:  # noqa: BLE001
        raise ArticleFetchError(f"network error fetching {url!r}: {exc}") from exc

    if html is None:
        raise ArticleFetchError(
            f"fetch_url returned None for {url!r} (timeout, non-200, or blocked)"
        )
    return str(html)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _extract(html: str, url: str) -> tuple[str, dict[str, str | None]]:
    """Run Trafilatura extraction; return (markdown_body, metadata_dict).

    Metadata keys: title, author, date, hostname, sitename.  Missing fields
    are returned as ``None``.
    """
    import trafilatura

    body = trafilatura.extract(
        html,
        output_format="markdown",
        with_metadata=False,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )

    meta_obj = trafilatura.extract_metadata(html, default_url=url)
    meta: dict[str, str | None] = {
        "title": getattr(meta_obj, "title", None) if meta_obj else None,
        "author": getattr(meta_obj, "author", None) if meta_obj else None,
        "date": getattr(meta_obj, "date", None) if meta_obj else None,
        "hostname": getattr(meta_obj, "hostname", None) if meta_obj else None,
        "sitename": getattr(meta_obj, "sitename", None) if meta_obj else None,
    }

    return (body or ""), meta


# ---------------------------------------------------------------------------
# Vault writing
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    """Turn ``text`` into a URL-safe slug (lowercase, hyphen-separated)."""
    lowered = text.lower().strip()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    if not slug:
        slug = "article"
    return slug[:max_len].rstrip("-") or "article"


def _vault_root() -> Path:
    root = os.environ.get("COMMONPLACE_VAULT_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / "commonplace"


def _write_vault_file(
    *,
    canonical_url: str,
    title: str | None,
    author: str | None,
    byline_date: str | None,
    hostname: str | None,
    body_md: str,
    fetched_at: datetime,
) -> Path:
    """Atomically write the article as a markdown file and return its path.

    Layout: ``<vault>/captures/YYYY/MM/<UTC-timestamp>-<slug>.md``.
    Uses ``.tmp`` + ``fsync`` + ``rename`` so readers never observe a half-
    written file.
    """
    vault_root = _vault_root()
    year = fetched_at.strftime("%Y")
    month = fetched_at.strftime("%m")
    out_dir = vault_root / "captures" / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = fetched_at.strftime("%Y-%m-%dT%H%M%SZ")
    slug_src = title or hostname or "article"
    slug = _slugify(slug_src)
    filename = f"{ts}-{slug}.md"
    final_path = out_dir / filename
    tmp_path = out_dir / f"{filename}.tmp"

    content = _render_markdown(
        canonical_url=canonical_url,
        title=title,
        author=author,
        byline_date=byline_date,
        hostname=hostname,
        body_md=body_md,
        fetched_at=fetched_at,
    )

    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(final_path)
    return final_path


def _yaml_escape(value: str) -> str:
    """Minimal YAML-safe escaping for a single-line scalar."""
    # Double-quote and escape backslashes + inner double quotes.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_markdown(
    *,
    canonical_url: str,
    title: str | None,
    author: str | None,
    byline_date: str | None,
    hostname: str | None,
    body_md: str,
    fetched_at: datetime,
) -> str:
    """Return the full frontmatter + body string to be written to disk."""
    lines: list[str] = ["---", "source: article"]
    lines.append(f"url: {_yaml_escape(canonical_url)}")
    if title:
        lines.append(f"title: {_yaml_escape(title)}")
    if author:
        lines.append(f"byline: {_yaml_escape(author)}")
    if byline_date:
        lines.append(f"byline_date: {_yaml_escape(byline_date)}")
    if hostname:
        lines.append(f"source_domain: {_yaml_escape(hostname)}")
    lines.append(f"fetched_at: {_yaml_escape(fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ'))}")
    lines.append("---")
    lines.append("")
    lines.append(body_md.rstrip() + "\n")
    return "\n".join(lines)
