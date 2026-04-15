"""Bluesky post ingest handler.

handle_bluesky_ingest(payload, conn) is the worker handler for the
'ingest_bluesky' job kind.  It fetches posts from the authenticated user's
Bluesky feed and ingests each one into the documents table with full embedding.

Payload shapes
--------------
  {"mode": "backfill"}
      Fetch ALL posts by the authenticated user, paging through
      app.bsky.feed.getAuthorFeed.

  {"mode": "delta", "since": "<iso8601>"}
      Fetch posts whose indexedAt timestamp is newer than ``since``.

  {"mode": "single", "uri": "at://..."}
      Ingest one specific post by AT URI.

Behaviour
---------
- Skips reposts (reason == ReasonRepost).  Only original posts and replies
  authored by the authenticated user are ingested.
- Each post is stored as a ``documents`` row with:
    content_type = 'bluesky_post'
    source_uri   = AT URI
    source_id    = AT URI   (unique index from migration 0003 deduplicates)
    author       = handle
    title        = first 80 chars of post text
    content_hash = SHA-256(text)
    status       = 'embedded' (after pipeline completes)
- ``pipeline.embed_document`` is called for every new post.
- Idempotent: posts whose source_id already exists in ``documents`` are skipped.
- Returns: {posts_fetched, posts_new, posts_skipped, elapsed_ms}
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

_PAGE_LIMIT = 100  # max records per getAuthorFeed page (Bluesky API cap)


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_bluesky_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _client: Any = None,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_bluesky' jobs.

    Parameters
    ----------
    payload:
        Must contain 'mode'.  See module docstring for shapes.
    conn:
        Open SQLite connection with all migrations applied.
    _client:
        Optional pre-authenticated atproto Client for tests.
    _embedder:
        Optional embedder function for tests (passed to embed_document).

    Returns
    -------
    dict with keys: posts_fetched, posts_new, posts_skipped, elapsed_ms.
    """
    t0 = time.monotonic()

    mode = payload.get("mode")
    if mode not in ("backfill", "delta", "single"):
        raise ValueError(
            f"ingest_bluesky payload has invalid 'mode': {mode!r}. "
            "Expected 'backfill', 'delta', or 'single'."
        )

    client = _get_client(_client)
    handle = _resolve_handle(client)

    if mode == "single":
        uri = payload.get("uri")
        if not isinstance(uri, str) or not uri.startswith("at://"):
            raise ValueError(
                f"ingest_bluesky mode='single' requires 'uri' (at://...): {payload!r}"
            )
        feed_items = _fetch_single(client, uri)
    elif mode == "delta":
        since = payload.get("since")
        if not isinstance(since, str) or not since:
            raise ValueError(
                f"ingest_bluesky mode='delta' requires 'since' (ISO 8601): {payload!r}"
            )
        feed_items = _fetch_all_posts(client, handle, since=since)
    else:
        feed_items = _fetch_all_posts(client, handle, since=None)

    posts_fetched = 0
    posts_new = 0
    posts_skipped = 0

    for item in feed_items:
        posts_fetched += 1
        new = _ingest_item(item, handle, conn, _embedder=_embedder)
        if new:
            posts_new += 1
        else:
            posts_skipped += 1

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "bluesky ingest complete mode=%s posts_fetched=%d posts_new=%d "
        "posts_skipped=%d elapsed_ms=%.0f",
        mode,
        posts_fetched,
        posts_new,
        posts_skipped,
        elapsed_ms,
    )
    return {
        "posts_fetched": posts_fetched,
        "posts_new": posts_new,
        "posts_skipped": posts_skipped,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# Feed fetching helpers
# ---------------------------------------------------------------------------


def _get_client(injected: Any) -> Any:
    """Return the injected client or a freshly authenticated one."""
    if injected is not None:
        return injected
    from commonplace_worker.bluesky_auth import get_authenticated_client

    return get_authenticated_client()


def _resolve_handle(client: Any) -> str:
    """Return the handle of the authenticated user from the client session."""
    session = getattr(client, "_session", None)
    if session is not None:
        handle = getattr(session, "handle", None)
        if handle:
            return str(handle)
    # Fallback: read from auth module
    from commonplace_worker.bluesky_auth import _read_handle  # noqa: PLC0415

    return _read_handle()


def _is_repost(item: Any) -> bool:
    """Return True if the feed item is a repost (not an original post)."""
    from atproto import models  # type: ignore[import-untyped]

    reason = getattr(item, "reason", None)
    return isinstance(reason, models.AppBskyFeedDefs.ReasonRepost)


def _fetch_all_posts(client: Any, handle: str, since: str | None) -> list[Any]:
    """Page through getAuthorFeed and return all original posts (no reposts).

    If ``since`` is provided, stops paging when all items on a page have
    indexedAt <= since (assumes API returns newest-first).
    """
    items: list[Any] = []
    cursor: str | None = None

    while True:
        try:
            resp = client.get_author_feed(actor=handle, cursor=cursor, limit=_PAGE_LIMIT)
        except Exception as exc:
            # Surface rate-limit errors clearly; don't hammer the API.
            msg = str(exc)
            if "rate" in msg.lower() or "429" in msg:
                raise RuntimeError(
                    f"Bluesky rate limit hit during feed fetch: {exc}. "
                    "Back off and retry later."
                ) from exc
            raise

        feed = getattr(resp, "feed", []) or []
        if not feed:
            break

        done = False
        for item in feed:
            if _is_repost(item):
                continue

            post = getattr(item, "post", None)
            if post is None:
                continue

            indexed_at: str = getattr(post, "indexed_at", "") or ""

            if since and indexed_at and indexed_at <= since:
                # API returns newest-first; once we reach older posts, we're done.
                done = True
                break

            items.append(item)

        cursor = getattr(resp, "cursor", None)
        if done or not cursor:
            break

    return items


def _fetch_single(client: Any, uri: str) -> list[Any]:
    """Fetch a single post by AT URI and return as a one-element list."""
    try:
        resp = client.get_posts(uris=[uri])
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch post {uri!r}: {exc}") from exc

    posts = getattr(resp, "posts", []) or []
    if not posts:
        logger.warning("No post found for URI %s", uri)
        return []

    # Wrap in a minimal feed-view-like object so _ingest_item works uniformly.
    class _FakeItem:
        post = posts[0]
        reason = None

    return [_FakeItem()]


# ---------------------------------------------------------------------------
# Ingest a single feed item
# ---------------------------------------------------------------------------


def _ingest_item(
    item: Any,
    handle: str,
    conn: sqlite3.Connection,
    *,
    _embedder: Any = None,
) -> bool:
    """Insert a single post into the documents table and embed it.

    Returns True if the post was new and inserted; False if it was skipped.
    """
    post = getattr(item, "post", None)
    if post is None:
        return False

    uri: str = getattr(post, "uri", "") or ""
    if not uri:
        logger.warning("Post missing URI, skipping")
        return False

    record = getattr(post, "record", None)
    text: str = ""
    if record is not None:
        text = getattr(record, "text", "") or ""

    if not text.strip():
        logger.debug("Post %s has no text, skipping", uri)
        return False

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    title = text[:80]
    source_id = uri

    # Idempotency: check by source_id (unique index covers content_type + source_id)
    existing = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'bluesky_post' AND source_id = ?",
        (source_id,),
    ).fetchone()
    if existing is not None:
        logger.debug("Post %s already ingested (document_id=%d), skipping", uri, existing["id"])
        return False

    # Insert documents row
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, author, content_hash, source_id, status)
            VALUES ('bluesky_post', ?, ?, ?, ?, ?, 'ingesting')
            """,
            (uri, title, handle, content_hash, source_id),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]

    # Chunk + embed via pipeline
    from commonplace_server.pipeline import embed_document  # noqa: PLC0415

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder

    embed_document(document_id, text, conn, **embed_kwargs)
    return True
