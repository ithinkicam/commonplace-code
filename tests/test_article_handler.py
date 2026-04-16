"""Tests for commonplace_worker/handlers/article.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from commonplace_db.db import migrate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite with sqlite-vec + migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[0.0] * 768 for _ in texts]


# A synthetic article HTML big enough for Trafilatura to treat as real
# reader-mode content (>200 chars after extraction).
_LONG_BODY = (
    "This is a thoughtful essay about the way cities breathe. "
    "The author walks through the streets observing small rhythms, "
    "noting how shopkeepers greet regulars, how light pools under "
    "awnings, and how the same corner changes personality between "
    "morning and dusk. "
) * 6

SAMPLE_HTML = f"""<!doctype html>
<html>
  <head>
    <title>A City at Rest</title>
    <meta name="author" content="Jane Doe">
    <meta property="article:published_time" content="2024-03-12T09:00:00Z">
  </head>
  <body>
    <article>
      <h1>A City at Rest</h1>
      <p>{_LONG_BODY}</p>
      <p>{_LONG_BODY}</p>
    </article>
  </body>
</html>
"""

# Paywall-style page: short teaser only.
PAYWALL_HTML = """<!doctype html>
<html>
  <head><title>Subscribers only</title></head>
  <body><div class="paywall"><p>Subscribe to read</p></div></body>
</html>
"""


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(root))
    return root


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_fetch_extract_embed(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.article import handle_article_ingest

    def fake_fetch(_url: str) -> str:
        return SAMPLE_HTML

    result = handle_article_ingest(
        {"url": "https://example.com/city-at-rest"},
        db_conn,
        _fetcher=fake_fetch,
        _embedder=_fake_embedder,
    )

    assert result["document_id"] is not None
    assert result["chunk_count"] >= 1
    assert result["url"] == "https://example.com/city-at-rest"
    assert result["title"] == "A City at Rest"
    assert result["elapsed_ms"] >= 0

    doc = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "article"
    assert doc["status"] == "embedded"
    assert doc["title"] == "A City at Rest"
    assert doc["author"] == "Jane Doe"
    assert doc["source_uri"] == "https://example.com/city-at-rest"
    assert doc["source_id"] == "https://example.com/city-at-rest"
    assert doc["raw_path"] is not None
    assert Path(doc["raw_path"]).exists()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_same_url(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.article import handle_article_ingest

    calls: list[int] = []

    def counting_embedder(texts: list[str], model: str) -> list[list[float]]:
        calls.append(len(texts))
        return [[0.0] * 768 for _ in texts]

    def fake_fetch(_url: str) -> str:
        return SAMPLE_HTML

    r1 = handle_article_ingest(
        {"url": "https://example.com/city-at-rest"},
        db_conn,
        _fetcher=fake_fetch,
        _embedder=counting_embedder,
    )
    r2 = handle_article_ingest(
        {"url": "https://example.com/city-at-rest"},
        db_conn,
        _fetcher=fake_fetch,
        _embedder=counting_embedder,
    )

    assert r1["document_id"] == r2["document_id"]
    count = db_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1
    # embedder only called on the first run
    assert len(calls) == 1
    # chunk_count is reported on the idempotent return, too
    assert r2["chunk_count"] == r1["chunk_count"]


def test_idempotent_fragment_stripped(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    """URL differing only by #fragment should dedupe to the same document."""
    from commonplace_worker.handlers.article import handle_article_ingest

    def fake_fetch(_url: str) -> str:
        return SAMPLE_HTML

    r1 = handle_article_ingest(
        {"url": "https://example.com/city-at-rest"},
        db_conn,
        _fetcher=fake_fetch,
        _embedder=_fake_embedder,
    )
    r2 = handle_article_ingest(
        {"url": "https://example.com/city-at-rest#section-2"},
        db_conn,
        _fetcher=fake_fetch,
        _embedder=_fake_embedder,
    )
    assert r1["document_id"] == r2["document_id"]


# ---------------------------------------------------------------------------
# Paywall / empty extraction
# ---------------------------------------------------------------------------


def test_paywall_raises_extraction_error(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.article import (
        ArticleExtractionError,
        handle_article_ingest,
    )

    def fake_fetch(_url: str) -> str:
        return PAYWALL_HTML

    with pytest.raises(ArticleExtractionError):
        handle_article_ingest(
            {"url": "https://paywalled.example.com/secret"},
            db_conn,
            _fetcher=fake_fetch,
            _embedder=_fake_embedder,
        )

    # No documents row should have been created on failure.
    count = db_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# Scheme validation
# ---------------------------------------------------------------------------


def test_non_http_scheme_rejected(db_conn: sqlite3.Connection, vault_dir: Path) -> None:
    from commonplace_worker.handlers.article import (
        ArticleFetchError,
        handle_article_ingest,
    )

    with pytest.raises(ArticleFetchError, match="unsupported URL scheme"):
        handle_article_ingest(
            {"url": "ftp://example.com/file.txt"},
            db_conn,
            _fetcher=lambda _u: SAMPLE_HTML,
            _embedder=_fake_embedder,
        )

    with pytest.raises(ArticleFetchError, match="unsupported URL scheme"):
        handle_article_ingest(
            {"url": "file:///etc/passwd"},
            db_conn,
            _fetcher=lambda _u: SAMPLE_HTML,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# Network / timeout errors
# ---------------------------------------------------------------------------


def test_network_error_surfaced_as_fetch_error(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.article import (
        ArticleFetchError,
        handle_article_ingest,
    )

    def boom(_url: str) -> str:
        raise ArticleFetchError("simulated timeout")

    with pytest.raises(ArticleFetchError, match="simulated timeout"):
        handle_article_ingest(
            {"url": "https://example.com/slow"},
            db_conn,
            _fetcher=boom,
            _embedder=_fake_embedder,
        )


def test_missing_url_raises_value_error(db_conn: sqlite3.Connection) -> None:
    from commonplace_worker.handlers.article import handle_article_ingest

    with pytest.raises(ValueError, match="missing 'url'"):
        handle_article_ingest({}, db_conn, _fetcher=lambda _u: "", _embedder=_fake_embedder)


# ---------------------------------------------------------------------------
# Vault file format
# ---------------------------------------------------------------------------


def test_vault_file_format(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    """Written file has YAML frontmatter + markdown body and lives under captures/YYYY/MM/."""
    from commonplace_worker.handlers.article import handle_article_ingest

    def fake_fetch(_url: str) -> str:
        return SAMPLE_HTML

    result = handle_article_ingest(
        {"url": "https://example.com/city-at-rest"},
        db_conn,
        _fetcher=fake_fetch,
        _embedder=_fake_embedder,
    )

    doc = db_conn.execute(
        "SELECT raw_path FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    path = Path(doc["raw_path"])
    assert path.exists()
    # Directory layout
    assert path.parent.parent.parent.name == "captures"
    # YYYY and MM look sane
    assert len(path.parent.parent.name) == 4  # year
    assert len(path.parent.name) == 2  # month

    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    # Frontmatter closes before any body content
    closing_idx = text.index("\n---\n", 4)
    frontmatter = text[4:closing_idx]
    body = text[closing_idx + len("\n---\n"):]

    # Frontmatter required fields
    assert "source: article" in frontmatter
    assert 'url: "https://example.com/city-at-rest"' in frontmatter
    assert 'title: "A City at Rest"' in frontmatter
    assert 'byline: "Jane Doe"' in frontmatter
    assert "fetched_at:" in frontmatter
    assert 'source_domain: "example.com"' in frontmatter

    # Body is non-empty markdown (Trafilatura extracted the paragraphs)
    assert len(body.strip()) > 100

    # No stray tmp files left behind
    leftovers = list(path.parent.glob("*.tmp"))
    assert leftovers == []


def test_chunks_and_embeddings_inserted(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.article import handle_article_ingest

    result = handle_article_ingest(
        {"url": "https://example.com/city-at-rest"},
        db_conn,
        _fetcher=lambda _u: SAMPLE_HTML,
        _embedder=_fake_embedder,
    )
    doc_id = result["document_id"]

    chunk_count = db_conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc_id,)
    ).fetchone()[0]
    embed_count = db_conn.execute(
        """SELECT COUNT(*) FROM embeddings e
           JOIN chunks c ON e.chunk_id = c.id
           WHERE c.document_id = ?""",
        (doc_id,),
    ).fetchone()[0]

    assert chunk_count >= 1
    assert embed_count == chunk_count
