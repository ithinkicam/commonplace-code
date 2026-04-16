"""Tests for commonplace_worker/handlers/bluesky_url.py.

All tests mock the atproto client and ``embed_document`` (via ``_embedder``).
No network calls, no keychain lookups, no Ollama dependency.  Vault writes
are redirected to a tmp_path via ``COMMONPLACE_VAULT_DIR``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from commonplace_db.db import migrate

# ---------------------------------------------------------------------------
# Fake-thread builders
# ---------------------------------------------------------------------------


def _make_node(
    *,
    uri: str,
    text: str,
    handle: str = "alice.bsky.social",
    did: str = "did:plc:alice",
    created_at: str = "2026-04-15T10:00:00.000Z",
    parent: Any = None,
    replies: list[Any] | None = None,
) -> MagicMock:
    """Build a fake ThreadViewPost-like MagicMock."""
    record = MagicMock()
    record.text = text
    record.created_at = created_at

    author = MagicMock()
    author.handle = handle
    author.did = did

    post = MagicMock()
    post.uri = uri
    post.record = record
    post.author = author
    post.indexed_at = created_at

    node = MagicMock()
    node.post = post
    node.parent = parent
    node.replies = replies or []
    return node


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite with migrations applied and sqlite-vec loaded."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect vault writes to tmp_path."""
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def mock_client() -> MagicMock:
    """Authenticated atproto Client mock with a resolve_handle helper."""
    client = MagicMock()
    session = MagicMock()
    session.handle = "me.bsky.social"
    session.did = "did:plc:me"
    client._session = session

    # Default handle resolution: alice.bsky.social -> did:plc:alice.
    def _resolve(handle: str) -> MagicMock:
        resp = MagicMock()
        resp.did = "did:plc:alice"
        return resp

    client.resolve_handle.side_effect = _resolve
    return client


def _basic_thread() -> MagicMock:
    """Thread: one parent → main → two replies (one short, one long)."""
    parent = _make_node(
        uri="at://did:plc:bob/app.bsky.feed.post/parent1",
        text="Kicking off a conversation about commonplace books.",
        handle="bob.bsky.social",
        did="did:plc:bob",
    )
    short_reply = _make_node(
        uri="at://did:plc:carol/app.bsky.feed.post/short",
        text="Yes!",  # <30 chars → dropped
        handle="carol.bsky.social",
        did="did:plc:carol",
    )
    long_reply = _make_node(
        uri="at://did:plc:dave/app.bsky.feed.post/long",
        text="I've been keeping one for years — it's changed how I read.",
        handle="dave.bsky.social",
        did="did:plc:dave",
    )
    main = _make_node(
        uri="at://did:plc:alice/app.bsky.feed.post/abc123",
        text="Commonplace books are underrated memory infrastructure.",
        handle="alice.bsky.social",
        did="did:plc:alice",
        parent=parent,
        replies=[short_reply, long_reply],
    )
    return main


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


def test_parse_url_with_handle() -> None:
    from commonplace_worker.handlers.bluesky_url import _parse_bluesky_url

    actor, rkey = _parse_bluesky_url(
        "https://bsky.app/profile/alice.bsky.social/post/abc123"
    )
    assert actor == "alice.bsky.social"
    assert rkey == "abc123"


def test_parse_url_with_did() -> None:
    from commonplace_worker.handlers.bluesky_url import _parse_bluesky_url

    actor, rkey = _parse_bluesky_url(
        "https://bsky.app/profile/did:plc:abc/post/xyz789"
    )
    assert actor == "did:plc:abc"
    assert rkey == "xyz789"


def test_parse_url_rejects_non_bsky() -> None:
    from commonplace_worker.handlers.bluesky_url import (
        BlueskyUrlError,
        _parse_bluesky_url,
    )

    with pytest.raises(BlueskyUrlError, match="bsky.app"):
        _parse_bluesky_url("https://twitter.com/alice/status/12345")


def test_parse_url_rejects_malformed() -> None:
    from commonplace_worker.handlers.bluesky_url import (
        BlueskyUrlError,
        _parse_bluesky_url,
    )

    with pytest.raises(BlueskyUrlError):
        _parse_bluesky_url("https://bsky.app/profile/alice")  # no /post/
    with pytest.raises(BlueskyUrlError):
        _parse_bluesky_url("")


def test_missing_url_in_payload(
    db_conn: sqlite3.Connection, mock_client: MagicMock, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.bluesky_url import (
        BlueskyUrlError,
        handle_bluesky_url_ingest,
    )

    with pytest.raises(BlueskyUrlError, match="missing 'url'"):
        handle_bluesky_url_ingest({}, db_conn, _client=mock_client)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_ingests_thread(
    db_conn: sqlite3.Connection,
    mock_client: MagicMock,
    vault_dir: Path,
) -> None:
    from commonplace_worker.handlers.bluesky_url import handle_bluesky_url_ingest

    resp = MagicMock()
    resp.thread = _basic_thread()
    mock_client.get_post_thread.return_value = resp

    url = "https://bsky.app/profile/alice.bsky.social/post/abc123"
    result = handle_bluesky_url_ingest(
        {"url": url, "inbox_file": None},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    assert result["url"] == url
    assert result["post_uri"] == "at://did:plc:alice/app.bsky.feed.post/abc123"
    # 1 parent + 1 main + 1 long reply (short reply filtered out)
    assert result["thread_post_count"] == 3
    assert result["chunk_count"] >= 1

    # Document row
    doc = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "bluesky_url"
    assert doc["source_uri"] == url
    assert doc["source_id"] == result["post_uri"]
    assert doc["author"] == "alice.bsky.social"
    assert doc["status"] == "embedded"

    # Vault file present
    raw_path = Path(doc["raw_path"])
    assert raw_path.exists()
    assert raw_path.is_file()
    assert str(vault_dir) in str(raw_path)

    # Called getPostThread with correct URI and depths
    mock_client.get_post_thread.assert_called_once()
    _, kwargs = mock_client.get_post_thread.call_args
    assert kwargs["uri"] == "at://did:plc:alice/app.bsky.feed.post/abc123"
    assert kwargs["depth"] == 10
    assert kwargs["parent_height"] == 10


def test_did_url_skips_resolve_handle(
    db_conn: sqlite3.Connection,
    mock_client: MagicMock,
    vault_dir: Path,
) -> None:
    from commonplace_worker.handlers.bluesky_url import handle_bluesky_url_ingest

    # Main post authored by did:plc:alice — but URL uses the DID directly.
    resp = MagicMock()
    resp.thread = _basic_thread()
    mock_client.get_post_thread.return_value = resp

    url = "https://bsky.app/profile/did:plc:alice/post/abc123"
    result = handle_bluesky_url_ingest(
        {"url": url, "inbox_file": None},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    assert result["post_uri"] == "at://did:plc:alice/app.bsky.feed.post/abc123"
    mock_client.resolve_handle.assert_not_called()


# ---------------------------------------------------------------------------
# Short-reply filter
# ---------------------------------------------------------------------------


def test_short_replies_are_dropped(
    db_conn: sqlite3.Connection,
    mock_client: MagicMock,
    vault_dir: Path,
) -> None:
    from commonplace_worker.handlers.bluesky_url import handle_bluesky_url_ingest

    resp = MagicMock()
    resp.thread = _basic_thread()
    mock_client.get_post_thread.return_value = resp

    result = handle_bluesky_url_ingest(
        {"url": "https://bsky.app/profile/alice.bsky.social/post/abc123"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    # Rendered markdown must not include the short "Yes!" reply text.
    raw_path = Path(
        db_conn.execute(
            "SELECT raw_path FROM documents WHERE id = ?",
            (result["document_id"],),
        ).fetchone()[0]
    )
    rendered = raw_path.read_text(encoding="utf-8")
    assert "Yes!" not in rendered
    assert "I've been keeping one for years" in rendered


def test_main_post_kept_even_if_short(
    db_conn: sqlite3.Connection,
    mock_client: MagicMock,
    vault_dir: Path,
) -> None:
    from commonplace_worker.handlers.bluesky_url import handle_bluesky_url_ingest

    short_main = _make_node(
        uri="at://did:plc:alice/app.bsky.feed.post/short",
        text="Hi.",  # <30 chars — but it's the main post, must be kept
        handle="alice.bsky.social",
        did="did:plc:alice",
    )
    resp = MagicMock()
    resp.thread = short_main
    mock_client.get_post_thread.return_value = resp

    result = handle_bluesky_url_ingest(
        {"url": "https://bsky.app/profile/alice.bsky.social/post/short"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )
    assert result["thread_post_count"] == 1

    raw_path = Path(
        db_conn.execute(
            "SELECT raw_path FROM documents WHERE id = ?",
            (result["document_id"],),
        ).fetchone()[0]
    )
    rendered = raw_path.read_text(encoding="utf-8")
    assert "Hi." in rendered


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_on_same_url(
    db_conn: sqlite3.Connection,
    mock_client: MagicMock,
    vault_dir: Path,
) -> None:
    from commonplace_worker.handlers.bluesky_url import handle_bluesky_url_ingest

    resp = MagicMock()
    resp.thread = _basic_thread()
    mock_client.get_post_thread.return_value = resp

    url = "https://bsky.app/profile/alice.bsky.social/post/abc123"

    embed_calls: list[int] = []

    def counting_embedder(texts: list[str], model: str) -> list[list[float]]:
        embed_calls.append(len(texts))
        return [[0.0] * 768 for _ in texts]

    first = handle_bluesky_url_ingest(
        {"url": url}, db_conn, _client=mock_client, _embedder=counting_embedder
    )
    second = handle_bluesky_url_ingest(
        {"url": url}, db_conn, _client=mock_client, _embedder=counting_embedder
    )

    assert first["document_id"] == second["document_id"]
    # Only one documents row
    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'bluesky_url'"
    ).fetchone()[0]
    assert count == 1
    # Second run does not re-fetch thread
    assert mock_client.get_post_thread.call_count == 1
    # Second run does not re-embed (embedder only called during first run)
    assert len(embed_calls) == 1


# ---------------------------------------------------------------------------
# Rendering format
# ---------------------------------------------------------------------------


def test_rendered_markdown_structure(
    db_conn: sqlite3.Connection,
    mock_client: MagicMock,
    vault_dir: Path,
) -> None:
    from commonplace_worker.handlers.bluesky_url import handle_bluesky_url_ingest

    resp = MagicMock()
    resp.thread = _basic_thread()
    mock_client.get_post_thread.return_value = resp

    result = handle_bluesky_url_ingest(
        {"url": "https://bsky.app/profile/alice.bsky.social/post/abc123"},
        db_conn,
        _client=mock_client,
        _embedder=_fake_embedder,
    )

    raw_path = Path(
        db_conn.execute(
            "SELECT raw_path FROM documents WHERE id = ?",
            (result["document_id"],),
        ).fetchone()[0]
    )
    rendered = raw_path.read_text(encoding="utf-8")

    # YAML frontmatter present with expected keys
    assert rendered.startswith("---\n")
    assert "source: bluesky\n" in rendered
    assert "url: https://bsky.app/profile/alice.bsky.social/post/abc123\n" in rendered
    assert "post_uri:" in rendered  # contains at:// (may be quoted)
    assert "author_handle: alice.bsky.social\n" in rendered
    assert "author_did:" in rendered  # may be quoted
    assert "thread_post_count: 3\n" in rendered

    # Section headings
    assert "## Thread context (parents)" in rendered
    assert "## Main post" in rendered
    assert "## Replies" in rendered

    # Order: parent appears before main appears before reply
    idx_parent = rendered.index("Kicking off a conversation")
    idx_main = rendered.index("Commonplace books are underrated")
    idx_reply = rendered.index("I've been keeping one for years")
    assert idx_parent < idx_main < idx_reply

    # Filename format: <ts>-bluesky-<rkey>.md under captures/YYYY/MM/
    assert raw_path.name.endswith("-bluesky-abc123.md")
    assert raw_path.parent.parent.parent.name == "captures"


# ---------------------------------------------------------------------------
# Login / fetch failure handling
# ---------------------------------------------------------------------------


def test_login_failure_surfaced_cleanly(
    db_conn: sqlite3.Connection, vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If bluesky_auth raises BlueskyAuthError, it propagates without crashing internals."""
    from commonplace_worker.bluesky_auth import BlueskyAuthError
    from commonplace_worker.handlers import bluesky_url as mod

    def boom() -> Any:
        raise BlueskyAuthError("keychain empty")

    # Monkeypatch get_authenticated_client via the auth module so the handler's
    # lazy import sees our stub.
    import commonplace_worker.bluesky_auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_authenticated_client", boom)

    with pytest.raises(BlueskyAuthError, match="keychain empty"):
        mod.handle_bluesky_url_ingest(
            {"url": "https://bsky.app/profile/alice.bsky.social/post/abc123"},
            db_conn,
        )

    # No documents row left behind
    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'bluesky_url'"
    ).fetchone()[0]
    assert count == 0


def test_non_bsky_url_rejected(
    db_conn: sqlite3.Connection, mock_client: MagicMock, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.bluesky_url import (
        BlueskyUrlError,
        handle_bluesky_url_ingest,
    )

    with pytest.raises(BlueskyUrlError, match="bsky.app"):
        handle_bluesky_url_ingest(
            {"url": "https://example.com/post/123"},
            db_conn,
            _client=mock_client,
        )


def test_not_found_thread_raises(
    db_conn: sqlite3.Connection, mock_client: MagicMock, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.bluesky_url import (
        BlueskyUrlError,
        handle_bluesky_url_ingest,
    )

    # Simulate NotFoundPost: a thread node with no .post attribute.
    not_found = MagicMock()
    # Make _is_viewable_thread return False by clearing post attr.
    not_found.post = None
    resp = MagicMock()
    resp.thread = not_found
    mock_client.get_post_thread.return_value = resp

    with pytest.raises(BlueskyUrlError, match="not viewable"):
        handle_bluesky_url_ingest(
            {"url": "https://bsky.app/profile/alice.bsky.social/post/missing"},
            db_conn,
            _client=mock_client,
            _embedder=_fake_embedder,
        )
