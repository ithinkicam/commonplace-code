"""Bluesky URL share-sheet ingest handler.

handle_bluesky_url_ingest(payload, conn) is the worker handler for a single
Bluesky post URL shared via the share-sheet.  Distinct from the Phase 2
historical-pull handler in ``commonplace_worker/handlers/bluesky.py``.

Behaviour
---------
1. Parse ``https://bsky.app/profile/<handle-or-did>/post/<rkey>``.
2. If the profile segment is a handle, resolve it to a DID via the
   authenticated atproto client.
3. Fetch the post *and its full thread* (parents up, replies down) via
   ``app.bsky.feed.getPostThread`` (``depth=10``, ``parent_height=10``).
4. Drop reply posts with fewer than 30 characters of visible text
   (matches the v5 rule: "Bluesky posts and thread replies; replies <30
   chars dropped").  The main post is kept regardless of length.
5. Render the thread as markdown and write atomically to the vault:
   ``~/commonplace/captures/YYYY/MM/<UTC-ts>-bluesky-<rkey>.md``.
6. Insert a ``documents`` row (``content_type='bluesky_url'``), chunk +
   embed the rendered thread, and return a result dict.

Idempotency
-----------
The canonical AT URI of the main post (``at://<did>/app.bsky.feed.post/<rkey>``)
is stored in ``documents.source_id``.  Re-running with the same URL returns
the existing ``document_id`` without a second insert, without a re-embed,
and without rewriting the vault file.

Payload
-------
    {"url": "https://bsky.app/profile/<handle-or-did>/post/<rkey>",
     "inbox_file": <str | None>}

Returns
-------
    {"document_id": int,
     "chunk_count": int,
     "elapsed_ms": float,
     "url": str,
     "post_uri": str,
     "thread_post_count": int}
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTENT_TYPE = "bluesky_url"
_REPLY_MIN_CHARS = 30  # replies shorter than this are dropped (main post exempt)
_DEFAULT_DEPTH = 10
_DEFAULT_PARENT_HEIGHT = 10

# https://bsky.app/profile/<handle-or-did>/post/<rkey>
# handle: e.g. alice.bsky.social
# did:    e.g. did:plc:abc123
# rkey:   URL-safe base32-ish identifier
_URL_RE = re.compile(
    r"^https?://bsky\.app/profile/(?P<actor>[^/]+)/post/(?P<rkey>[A-Za-z0-9._~-]+)/?$"
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BlueskyUrlError(ValueError):
    """Raised for malformed input URLs or unexpected thread shapes."""


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


def _parse_bluesky_url(url: str) -> tuple[str, str]:
    """Return ``(actor, rkey)`` for a ``bsky.app`` post URL.

    ``actor`` is either a handle (``alice.bsky.social``) or a DID
    (``did:plc:...``).  Raises :class:`BlueskyUrlError` for any other URL.
    """
    if not isinstance(url, str) or not url:
        raise BlueskyUrlError(f"Bluesky URL must be a non-empty string, got {url!r}")

    m = _URL_RE.match(url.strip())
    if m is None:
        raise BlueskyUrlError(
            f"Not a bsky.app post URL: {url!r}. "
            "Expected https://bsky.app/profile/<handle-or-did>/post/<rkey>"
        )

    return m.group("actor"), m.group("rkey")


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_bluesky_url_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _client: Any = None,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for a Bluesky URL share-sheet capture.

    See module docstring for payload/return shapes.
    """
    t0 = time.monotonic()

    url = payload.get("url")
    if not isinstance(url, str) or not url:
        raise BlueskyUrlError(
            f"bluesky_url payload missing 'url': {payload!r}"
        )

    actor, rkey = _parse_bluesky_url(url)

    # Authenticate (or reuse injected client).
    client = _get_client(_client)

    # Resolve handle -> did so the AT URI is canonical.
    did = _resolve_actor_to_did(client, actor)
    post_uri = f"at://{did}/app.bsky.feed.post/{rkey}"

    # Idempotency check BEFORE fetching — if we've seen this URI, return early.
    existing = conn.execute(
        "SELECT id FROM documents WHERE content_type = ? AND source_id = ?",
        (_CONTENT_TYPE, post_uri),
    ).fetchone()
    if existing is not None:
        existing_id: int = existing["id"]
        # Chunks may or may not exist if a prior run crashed mid-embed; count them.
        chunk_count = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
        ).fetchone()[0]
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "bluesky_url already ingested url=%s document_id=%d (skipping)",
            url,
            existing_id,
        )
        return {
            "document_id": existing_id,
            "chunk_count": int(chunk_count),
            "elapsed_ms": elapsed_ms,
            "url": url,
            "post_uri": post_uri,
            "thread_post_count": 0,  # not re-fetched
        }

    # Fetch thread.
    thread_view = _fetch_thread(client, post_uri)

    # Flatten to (parents, main, replies) lists of dicts with text + metadata.
    parents, main, replies = _flatten_thread(thread_view)
    if main is None:
        raise BlueskyUrlError(
            f"Thread response for {post_uri!r} has no usable main post."
        )

    # Drop short replies.
    replies_kept = [r for r in replies if len(r["text"]) >= _REPLY_MIN_CHARS]

    thread_post_count = len(parents) + 1 + len(replies_kept)

    # Render markdown.
    rendered = _render_thread(
        url=url,
        post_uri=post_uri,
        main=main,
        parents=parents,
        replies=replies_kept,
        thread_post_count=thread_post_count,
    )

    # Write to vault atomically.
    vault_path = _write_vault_file(rkey=rkey, rendered=rendered)

    # Insert documents row.
    content_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    title = (main["text"][:80] or f"Bluesky post {rkey}")
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, author, content_hash,
                 source_id, raw_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ingesting')
            """,
            (
                _CONTENT_TYPE,
                url,
                title,
                main["author_handle"],
                content_hash,
                post_uri,
                str(vault_path),
            ),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]

    # Embed the rendered thread (includes main + kept replies).
    from commonplace_server.pipeline import embed_document  # noqa: PLC0415

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    result = embed_document(document_id, rendered, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "bluesky_url ingest complete url=%s document_id=%d chunks=%d "
        "thread_posts=%d elapsed_ms=%.0f",
        url,
        document_id,
        result.chunk_count,
        thread_post_count,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "url": url,
        "post_uri": post_uri,
        "thread_post_count": thread_post_count,
    }


# ---------------------------------------------------------------------------
# atproto client + thread helpers
# ---------------------------------------------------------------------------


def _get_client(injected: Any) -> Any:
    """Return the injected client or a freshly authenticated one.

    Surfaces ``BlueskyAuthError`` as-is so the worker can handle it cleanly
    rather than crashing.
    """
    if injected is not None:
        return injected

    # Import locally so unit tests that inject a mock don't need keychain
    # or network access.
    from commonplace_worker.bluesky_auth import (  # noqa: PLC0415
        BlueskyAuthError,
        get_authenticated_client,
    )

    try:
        return get_authenticated_client()
    except BlueskyAuthError:
        # Re-raise untouched — the worker (3.8) decides whether to retry/park.
        raise


def _resolve_actor_to_did(client: Any, actor: str) -> str:
    """Return the DID for ``actor`` (which may already be a DID)."""
    if actor.startswith("did:"):
        return actor

    try:
        resp = client.resolve_handle(actor)
    except Exception as exc:  # noqa: BLE001
        raise BlueskyUrlError(
            f"Failed to resolve Bluesky handle {actor!r}: {exc}"
        ) from exc

    did = getattr(resp, "did", None) or (resp.get("did") if isinstance(resp, dict) else None)
    if not did:
        raise BlueskyUrlError(
            f"resolve_handle({actor!r}) returned no DID."
        )
    return str(did)


def _fetch_thread(client: Any, post_uri: str) -> Any:
    """Call ``get_post_thread`` and return the ``thread`` field.

    Raises :class:`BlueskyUrlError` for NotFoundPost / BlockedPost on the root.
    """
    try:
        resp = client.get_post_thread(
            uri=post_uri,
            depth=_DEFAULT_DEPTH,
            parent_height=_DEFAULT_PARENT_HEIGHT,
        )
    except Exception as exc:  # noqa: BLE001
        raise BlueskyUrlError(
            f"getPostThread failed for {post_uri!r}: {exc}"
        ) from exc

    thread = getattr(resp, "thread", None)
    if thread is None:
        raise BlueskyUrlError(f"Empty thread response for {post_uri!r}.")

    # NotFoundPost / BlockedPost on the requested URI itself means we can't ingest.
    # We detect these by duck-typing: a usable ThreadViewPost has a .post attr
    # with a .record containing .text.
    if not _is_viewable_thread(thread):
        raise BlueskyUrlError(
            f"Post {post_uri!r} is not viewable (not found, blocked, or unsupported)."
        )

    return thread


def _is_viewable_thread(node: Any) -> bool:
    """Return True if ``node`` looks like a usable ThreadViewPost."""
    post = getattr(node, "post", None)
    if post is None:
        return False
    record = getattr(post, "record", None)
    return record is not None and hasattr(record, "text")


def _extract_post(node: Any) -> dict[str, Any] | None:
    """Extract a flat dict from a ThreadViewPost-like node.

    Returns ``None`` for NotFoundPost / BlockedPost / other non-viewable nodes.
    """
    if not _is_viewable_thread(node):
        return None
    post = node.post
    record = post.record
    author = getattr(post, "author", None)
    author_handle = getattr(author, "handle", "") if author is not None else ""
    author_did = getattr(author, "did", "") if author is not None else ""
    return {
        "uri": getattr(post, "uri", "") or "",
        "text": (getattr(record, "text", "") or "").strip(),
        "author_handle": str(author_handle) if author_handle else "",
        "author_did": str(author_did) if author_did else "",
        "indexed_at": getattr(post, "indexed_at", "") or "",
        "created_at": getattr(record, "created_at", "") or "",
    }


def _flatten_thread(
    thread: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    """Flatten a ThreadViewPost into (parents [root→…], main, replies).

    ``parents`` is ordered from the root parent down to the immediate parent
    of ``main``.  ``replies`` is a flat list of all descendant replies in
    depth-first order (children before grand-children); non-viewable nodes
    are skipped.
    """
    main = _extract_post(thread)

    # Walk parents upward, then reverse so root is first.
    parents: list[dict[str, Any]] = []
    node = getattr(thread, "parent", None)
    while node is not None:
        extracted = _extract_post(node)
        if extracted is not None:
            parents.append(extracted)
        node = getattr(node, "parent", None)
    parents.reverse()

    # DFS replies.
    replies: list[dict[str, Any]] = []

    def _walk(n: Any) -> None:
        kids = getattr(n, "replies", None) or []
        for kid in kids:
            extracted = _extract_post(kid)
            if extracted is not None:
                replies.append(extracted)
            _walk(kid)

    _walk(thread)

    return parents, main, replies


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _render_thread(
    *,
    url: str,
    post_uri: str,
    main: dict[str, Any],
    parents: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    thread_post_count: int,
) -> str:
    """Render the thread as YAML-frontmatter markdown.

    Order: root parent → … → main → replies (flat).
    """
    posted_at = main.get("created_at") or main.get("indexed_at") or ""
    frontmatter_lines = [
        "---",
        "source: bluesky",
        f"url: {url}",
        f"post_uri: {post_uri}",
        f"author_handle: {_yaml_scalar(main['author_handle'])}",
        f"author_did: {_yaml_scalar(main['author_did'])}",
        f"posted_at: {_yaml_scalar(posted_at)}",
        f"thread_post_count: {thread_post_count}",
        "---",
        "",
    ]

    body_lines: list[str] = []
    if parents:
        body_lines.append("## Thread context (parents)")
        body_lines.append("")
        for p in parents:
            body_lines.extend(_render_post_block(p, heading="Parent"))

    body_lines.append("## Main post")
    body_lines.append("")
    body_lines.extend(_render_post_block(main, heading="Post"))

    if replies:
        body_lines.append("## Replies")
        body_lines.append("")
        for r in replies:
            body_lines.extend(_render_post_block(r, heading="Reply"))

    return "\n".join(frontmatter_lines + body_lines).rstrip() + "\n"


def _render_post_block(post: dict[str, Any], *, heading: str) -> list[str]:
    """Return markdown lines for a single post."""
    author = post["author_handle"] or "(unknown)"
    when = post.get("created_at") or post.get("indexed_at") or ""
    header = f"### {heading} — @{author}"
    if when:
        header += f"  ({when})"
    block = [header, ""]
    text = post["text"] or "(no text)"
    # Indent each line with "> " for a blockquote-style thread rendering.
    for line in text.splitlines() or [text]:
        block.append(f"> {line}")
    block.append("")
    return block


def _yaml_scalar(value: str) -> str:
    """Quote a YAML scalar if it needs quoting; otherwise return bare."""
    if value == "":
        return '""'
    # Quote if value contains YAML-reserved chars or starts with reserved.
    if any(c in value for c in (":", "#", "'", '"', "\n", "\t")) or value[0] in "-?[]{}!&*|>%@`":
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


# ---------------------------------------------------------------------------
# Vault write (atomic)
# ---------------------------------------------------------------------------


def _vault_dir() -> Path:
    """Return the root vault directory.

    Honors ``COMMONPLACE_VAULT_DIR``; defaults to ``~/commonplace``.
    """
    env = os.environ.get("COMMONPLACE_VAULT_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "commonplace"


def _write_vault_file(*, rkey: str, rendered: str) -> Path:
    """Atomically write the rendered thread to the vault and return the path."""
    now = datetime.now(UTC)
    year = now.strftime("%Y")
    month = now.strftime("%m")
    ts = now.strftime("%Y-%m-%dT%H%M%SZ")

    folder = _vault_dir() / "captures" / year / month
    folder.mkdir(parents=True, exist_ok=True)

    # Sanitise rkey for filesystem use (it should already be safe; belt & braces).
    safe_rkey = re.sub(r"[^A-Za-z0-9._-]", "_", rkey)
    filename = f"{ts}-bluesky-{safe_rkey}.md"
    final_path = folder / filename
    tmp_path = folder / (filename + ".tmp")

    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(rendered)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(final_path)

    return final_path
