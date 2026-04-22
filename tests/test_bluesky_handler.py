"""Tests for commonplace_worker/handlers/bluesky.py.

All tests mock the atproto client and embed_document — no network calls,
no Ollama dependency.  Uses in-memory SQLite with all migrations applied.
"""

from __future__ import annotations

import hashlib
import sqlite3
from unittest.mock import MagicMock

import pytest

from commonplace_db.db import migrate

# ---------------------------------------------------------------------------
# Helpers for building fake atproto objects
# ---------------------------------------------------------------------------


def _make_post(
    uri: str,
    text: str,
    handle: str = "ithinkicam.bsky.social",
    indexed_at: str = "2026-01-01T00:00:00.000Z",
    is_repost: bool = False,
) -> MagicMock:
    """Build a fake FeedViewPost-like MagicMock."""
    record = MagicMock()
    record.text = text

    post = MagicMock()
    post.uri = uri
    post.record = record
    post.indexed_at = indexed_at

    author = MagicMock()
    author.handle = handle
    post.author = author

    item = MagicMock()
    item.post = post

    if is_repost:
        from atproto import models  # type: ignore[import-untyped]

        reason = MagicMock(spec=models.AppBskyFeedDefs.ReasonRepost)
        item.reason = reason
    else:
        item.reason = None

    return item


def _make_feed_response(items: list[MagicMock], cursor: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.feed = items
    resp.cursor = cursor
    return resp


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    """Return zero-vectors of dimension 768."""
    return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite with all migrations applied (including vec0 table)."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def mock_client() -> MagicMock:
    """A mock atproto Client with a valid session."""
    client = MagicMock()
    session = MagicMock()
    session.handle = "ithinkicam.bsky.social"
    session.did = "did:plc:test"
    session.access_jwt = "access.jwt"
    session.refresh_jwt = "refresh.jwt"
    client._session = session
    return client


# ---------------------------------------------------------------------------
# Test: single mode
# ---------------------------------------------------------------------------


def test_single_mode_inserts_document(db_conn: sqlite3.Connection, mock_client: MagicMock) -> None:
    """mode='single' inserts a documents row and calls embed_document."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    uri = "at://did:plc:test/app.bsky.feed.post/abc123"
    text = "Hello from Bluesky!"

    post_obj = MagicMock()
    post_obj.uri = uri
    record = MagicMock()
    record.text = text
    post_obj.record = record
    post_obj.indexed_at = "2026-01-01T00:00:00.000Z"

    resp = MagicMock()
    resp.posts = [post_obj]
    mock_client.get_posts.return_value = resp

    result = handle_bluesky_ingest(
        {"mode": "single", "uri": uri},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    assert result["posts_fetched"] == 1
    assert result["posts_new"] == 1
    assert result["posts_skipped"] == 0

    doc = db_conn.execute(
        "SELECT * FROM documents WHERE source_id = ?", (uri,)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "bluesky_post"
    assert doc["source_uri"] == uri
    assert doc["author"] == "ithinkicam.bsky.social"
    assert doc["title"] == text[:80]
    assert doc["content_hash"] == hashlib.sha256(text.encode()).hexdigest()
    assert doc["status"] == "embedded"


# ---------------------------------------------------------------------------
# Test: backfill paginates through multiple pages
# ---------------------------------------------------------------------------


def test_backfill_paginates(db_conn: sqlite3.Connection, mock_client: MagicMock) -> None:
    """mode='backfill' pages through getAuthorFeed until cursor is None."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    handle = "ithinkicam.bsky.social"

    page1_items = [
        _make_post(f"at://did:plc:test/app.bsky.feed.post/p{i}", f"Post number {i}", handle)
        for i in range(3)
    ]
    page2_items = [
        _make_post(f"at://did:plc:test/app.bsky.feed.post/p{i}", f"Post number {i}", handle)
        for i in range(3, 5)
    ]

    mock_client.get_author_feed.side_effect = [
        _make_feed_response(page1_items, cursor="cursor1"),
        _make_feed_response(page2_items, cursor=None),
    ]

    result = handle_bluesky_ingest(
        {"mode": "backfill"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    assert result["posts_fetched"] == 5
    assert result["posts_new"] == 5
    assert mock_client.get_author_feed.call_count == 2

    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'bluesky_post'"
    ).fetchone()[0]
    assert count == 5


# ---------------------------------------------------------------------------
# Test: embed pipeline is called with post text
# ---------------------------------------------------------------------------


def test_embed_called_with_post_text(db_conn: sqlite3.Connection, mock_client: MagicMock) -> None:
    """embed_document is called with the exact post text."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    uri = "at://did:plc:test/app.bsky.feed.post/embed_test"
    text = "This text should be embedded exactly."

    item = _make_post(uri, text)
    mock_client.get_author_feed.side_effect = [
        _make_feed_response([item], cursor=None),
    ]

    embedded_texts: list[str] = []

    def tracking_embedder(texts: list[str], model: str) -> list[list[float]]:
        embedded_texts.extend(texts)
        return [[0.0] * 768 for _ in texts]

    handle_bluesky_ingest(
        {"mode": "backfill"},
        db_conn,
        _client=mock_client,
        _embedder=tracking_embedder,
    )

    assert len(embedded_texts) >= 1
    assert text in embedded_texts


# ---------------------------------------------------------------------------
# Test: idempotency — same source_id skipped on re-run
# ---------------------------------------------------------------------------


def test_idempotent_rerun_skips_existing(
    db_conn: sqlite3.Connection, mock_client: MagicMock
) -> None:
    """Re-running backfill with same posts does not create duplicate documents rows."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    uri = "at://did:plc:test/app.bsky.feed.post/idem1"
    item = _make_post(uri, "Idempotent post text.")

    mock_client.get_author_feed.side_effect = [
        _make_feed_response([item], cursor=None),
    ]

    result1 = handle_bluesky_ingest(
        {"mode": "backfill"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )
    assert result1["posts_new"] == 1

    # Reset side_effect for second run
    mock_client.get_author_feed.side_effect = [
        _make_feed_response([item], cursor=None),
    ]

    result2 = handle_bluesky_ingest(
        {"mode": "backfill"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )
    assert result2["posts_new"] == 0
    assert result2["posts_skipped"] == 1

    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'bluesky_post'"
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Test: idempotency — colliding content_hash skipped (different source_id)
# ---------------------------------------------------------------------------


def test_duplicate_content_hash_skipped(
    db_conn: sqlite3.Connection, mock_client: MagicMock
) -> None:
    """Two posts with different source_id but identical text are not double-inserted.

    Regression: documents.content_hash has a global UNIQUE constraint, so a second
    insert with the same SHA-256 would raise sqlite3.IntegrityError. The handler
    must pre-check content_hash and skip.
    """
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    text = "gm"
    first = _make_post("at://did:plc:test/app.bsky.feed.post/day1", text)
    second = _make_post("at://did:plc:test/app.bsky.feed.post/day2", text)

    mock_client.get_author_feed.side_effect = [
        _make_feed_response([first, second], cursor=None),
    ]

    result = handle_bluesky_ingest(
        {"mode": "backfill"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    assert result["posts_fetched"] == 2
    assert result["posts_new"] == 1
    assert result["posts_skipped"] == 1

    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'bluesky_post'"
    ).fetchone()[0]
    assert count == 1
    # The first post (day1) is the one that survived
    doc = db_conn.execute(
        "SELECT source_id FROM documents WHERE content_type = 'bluesky_post'"
    ).fetchone()
    assert "day1" in doc["source_id"]


# ---------------------------------------------------------------------------
# Test: reposts are skipped
# ---------------------------------------------------------------------------


def test_reposts_are_skipped(db_conn: sqlite3.Connection, mock_client: MagicMock) -> None:
    """Reposted items are not ingested."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    original = _make_post(
        "at://did:plc:test/app.bsky.feed.post/orig1",
        "Original post",
        is_repost=False,
    )
    repost = _make_post(
        "at://did:plc:other/app.bsky.feed.post/other1",
        "Someone else's post",
        is_repost=True,
    )

    mock_client.get_author_feed.side_effect = [
        _make_feed_response([original, repost], cursor=None),
    ]

    result = handle_bluesky_ingest(
        {"mode": "backfill"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    # Only the original was counted/fetched; repost filtered before counting
    assert result["posts_fetched"] == 1
    assert result["posts_new"] == 1

    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'bluesky_post'"
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Test: delta mode stops at 'since' boundary
# ---------------------------------------------------------------------------


def test_delta_mode_stops_at_since(db_conn: sqlite3.Connection, mock_client: MagicMock) -> None:
    """mode='delta' with since=T ignores posts at or before T (newest-first feed)."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    newer = _make_post(
        "at://did:plc:test/app.bsky.feed.post/newer",
        "New post",
        indexed_at="2026-03-01T00:00:00.000Z",
    )
    older = _make_post(
        "at://did:plc:test/app.bsky.feed.post/older",
        "Old post",
        indexed_at="2026-01-01T00:00:00.000Z",
    )

    mock_client.get_author_feed.side_effect = [
        _make_feed_response([newer, older], cursor="would-not-follow"),
    ]

    result = handle_bluesky_ingest(
        {"mode": "delta", "since": "2026-02-01T00:00:00.000Z"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    assert result["posts_new"] == 1
    doc = db_conn.execute(
        "SELECT source_id FROM documents WHERE content_type = 'bluesky_post'"
    ).fetchone()
    assert "newer" in doc["source_id"]


# ---------------------------------------------------------------------------
# Test: invalid payload raises
# ---------------------------------------------------------------------------


def test_invalid_mode_raises(db_conn: sqlite3.Connection, mock_client: MagicMock) -> None:
    """An unknown mode raises ValueError."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    with pytest.raises(ValueError, match="invalid 'mode'"):
        handle_bluesky_ingest({"mode": "unknown"}, db_conn, _client=mock_client)


def test_single_mode_missing_uri_raises(
    db_conn: sqlite3.Connection, mock_client: MagicMock
) -> None:
    """mode='single' without 'uri' raises ValueError."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    with pytest.raises(ValueError, match="requires 'uri'"):
        handle_bluesky_ingest({"mode": "single"}, db_conn, _client=mock_client)


def test_delta_mode_missing_since_raises(
    db_conn: sqlite3.Connection, mock_client: MagicMock
) -> None:
    """mode='delta' without 'since' raises ValueError."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    with pytest.raises(ValueError, match="requires 'since'"):
        handle_bluesky_ingest({"mode": "delta"}, db_conn, _client=mock_client)


# ---------------------------------------------------------------------------
# Test: chunks + embeddings rows are created
# ---------------------------------------------------------------------------


def test_chunks_and_embeddings_created(
    db_conn: sqlite3.Connection, mock_client: MagicMock
) -> None:
    """After ingest, chunks and embeddings rows exist for the new document."""
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest

    item = _make_post(
        "at://did:plc:test/app.bsky.feed.post/chunks_test",
        "A Bluesky post that needs chunking and embedding.",
    )
    mock_client.get_author_feed.side_effect = [
        _make_feed_response([item], cursor=None),
    ]

    handle_bluesky_ingest(
        {"mode": "backfill"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    doc = db_conn.execute(
        "SELECT id FROM documents WHERE content_type = 'bluesky_post'"
    ).fetchone()
    assert doc is not None

    chunk_count = db_conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc["id"],)
    ).fetchone()[0]
    embed_count = db_conn.execute(
        "SELECT COUNT(*) FROM embeddings e JOIN chunks c ON e.chunk_id = c.id "
        "WHERE c.document_id = ?",
        (doc["id"],),
    ).fetchone()[0]

    assert chunk_count >= 1
    assert embed_count == chunk_count
