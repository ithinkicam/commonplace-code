"""Integration tests for embed_document() against in-memory SQLite."""

from __future__ import annotations

import sqlite3

import pytest

from commonplace_db import connect, migrate
from commonplace_server.pipeline import EmbedResult, embed_document

_DIM = 768


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    """Return distinct fixed vectors (index * 0.001 fill) for each text."""
    return [[float(i) * 0.001] * _DIM for i in range(len(texts))]


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _insert_document(conn: sqlite3.Connection, title: str = "Test Doc") -> int:
    cur = conn.execute(
        "INSERT INTO documents (content_type, title) VALUES (?, ?)",
        ("capture", title),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Basic flow
# ---------------------------------------------------------------------------


def test_embed_document_returns_embed_result(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    text = "First paragraph.\n\nSecond paragraph."
    result = embed_document(doc_id, text, db, _embedder=_fake_embedder)
    assert isinstance(result, EmbedResult)
    assert result.chunk_count >= 1
    assert result.total_tokens > 0
    assert result.model == "nomic-embed-text"
    assert result.elapsed_ms >= 0


def test_chunks_rows_inserted(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    result = embed_document(doc_id, text, db, _embedder=_fake_embedder)
    count = db.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc_id,)
    ).fetchone()[0]
    assert count == result.chunk_count


def test_embeddings_rows_inserted(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    text = "Alpha paragraph.\n\nBeta paragraph."
    result = embed_document(doc_id, text, db, _embedder=_fake_embedder)
    emb_count = db.execute(
        """
        SELECT COUNT(*) FROM embeddings e
        JOIN chunks c ON e.chunk_id = c.id
        WHERE c.document_id = ?
        """,
        (doc_id,),
    ).fetchone()[0]
    assert emb_count == result.chunk_count


def test_chunk_vectors_rows_inserted(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    text = "One.\n\nTwo.\n\nThree."
    result = embed_document(doc_id, text, db, _embedder=_fake_embedder)
    vec_count = db.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
    assert vec_count == result.chunk_count


def test_document_status_becomes_embedded(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    text = "Some content here."
    embed_document(doc_id, text, db, _embedder=_fake_embedder)
    status = db.execute(
        "SELECT status FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()["status"]
    assert status == "embedded"


def test_token_count_stored(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    text = "Hello world, this is a test."
    embed_document(doc_id, text, db, _embedder=_fake_embedder)
    row = db.execute(
        "SELECT token_count FROM chunks WHERE document_id = ?", (doc_id,)
    ).fetchone()
    assert row["token_count"] is not None
    assert row["token_count"] > 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_second_call_is_noop(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    text = "Content that should only be embedded once."

    result1 = embed_document(doc_id, text, db, _embedder=_fake_embedder)
    result2 = embed_document(doc_id, text, db, _embedder=_fake_embedder)

    # Chunk count and token count should match
    assert result1.chunk_count == result2.chunk_count

    # Database should still have the same number of rows
    chunk_count = db.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc_id,)
    ).fetchone()[0]
    assert chunk_count == result1.chunk_count

    vec_count = db.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
    assert vec_count == result1.chunk_count


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_text_marks_embedded_no_chunks(db: sqlite3.Connection) -> None:
    doc_id = _insert_document(db)
    result = embed_document(doc_id, "", db, _embedder=_fake_embedder)
    assert result.chunk_count == 0
    status = db.execute(
        "SELECT status FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()["status"]
    assert status == "embedded"


# ---------------------------------------------------------------------------
# embed_text_override
# ---------------------------------------------------------------------------


def test_no_override_embedder_receives_chunk_text_verbatim(db: sqlite3.Connection) -> None:
    """Regression guard: without an override, embedder input == chunk.text for every chunk."""
    from commonplace_server.chunking import chunk_text

    doc_id = _insert_document(db)
    text = "Alpha paragraph.\n\nBeta paragraph."
    expected_chunks = chunk_text(text)
    expected_texts = [c.text for c in expected_chunks]

    captured: list[list[str]] = []

    def capturing_embedder(texts: list[str], model: str) -> list[list[float]]:
        captured.append(list(texts))
        return _fake_embedder(texts, model)

    embed_document(doc_id, text, db, _embedder=capturing_embedder)

    assert len(captured) == 1, "embedder should be called exactly once"
    assert captured[0] == expected_texts, (
        "embedder must receive [c.text for c in chunks] byte-identical when no override"
    )


def test_override_embedder_receives_prefixed_strings(db: sqlite3.Connection) -> None:
    """Override path: embedder sees transformed strings; DB chunks.text stays raw."""
    doc_id = _insert_document(db)
    text = "Hello world."

    captured: list[list[str]] = []

    def capturing_embedder(texts: list[str], model: str) -> list[list[float]]:
        captured.append(list(texts))
        return _fake_embedder(texts, model)

    embed_document(
        doc_id,
        text,
        db,
        _embedder=capturing_embedder,
        embed_text_override=lambda c: f"PREFIX::{c.text}",
    )

    assert len(captured) == 1
    for sent, stored_row in zip(
        captured[0],
        db.execute("SELECT text FROM chunks WHERE document_id = ? ORDER BY chunk_index", (doc_id,)).fetchall(),
    ):
        raw_text = stored_row["text"]
        assert sent == f"PREFIX::{raw_text}", "embedder should see prefixed string"
        assert not raw_text.startswith("PREFIX::"), "DB must hold raw text, not prefixed"


def test_override_multi_chunk_alignment(db: sqlite3.Connection) -> None:
    """Override + multi-chunk: composed strings are 1:1 aligned with chunks."""
    from commonplace_server.chunking import chunk_text

    doc_id = _insert_document(db)
    # Use three distinct paragraphs to guarantee multiple chunks
    text = "Para one.\n\nPara two.\n\nPara three."
    expected_chunks = chunk_text(text)

    captured: list[list[str]] = []

    def capturing_embedder(texts: list[str], model: str) -> list[list[float]]:
        captured.append(list(texts))
        return _fake_embedder(texts, model)

    result = embed_document(
        doc_id,
        text,
        db,
        _embedder=capturing_embedder,
        embed_text_override=lambda c: f"WRAP[{c.text}]",
    )

    assert result.chunk_count == len(expected_chunks)
    assert len(captured) == 1
    assert len(captured[0]) == len(expected_chunks)
    for sent, chunk in zip(captured[0], expected_chunks):
        assert sent == f"WRAP[{chunk.text}]"

    # DB stores raw text
    db_texts = [
        r["text"]
        for r in db.execute(
            "SELECT text FROM chunks WHERE document_id = ? ORDER BY chunk_index", (doc_id,)
        ).fetchall()
    ]
    assert db_texts == [c.text for c in expected_chunks]


def test_override_idempotency_unchanged(db: sqlite3.Connection) -> None:
    """Running twice with an override is still a no-op the second time."""
    doc_id = _insert_document(db)
    text = "Idempotency check with override."

    call_count = 0

    def counting_embedder(texts: list[str], model: str) -> list[list[float]]:
        nonlocal call_count
        call_count += 1
        return _fake_embedder(texts, model)

    result1 = embed_document(
        doc_id,
        text,
        db,
        _embedder=counting_embedder,
        embed_text_override=lambda c: f"OVERRIDE::{c.text}",
    )
    result2 = embed_document(
        doc_id,
        text,
        db,
        _embedder=counting_embedder,
        embed_text_override=lambda c: f"OVERRIDE::{c.text}",
    )

    assert call_count == 1, "embedder must only be called once (idempotency guard)"
    assert result1.chunk_count == result2.chunk_count


def test_override_exception_propagates(db: sqlite3.Connection) -> None:
    """If embed_text_override raises, embed_document propagates the exception."""
    doc_id = _insert_document(db)
    text = "Some content."

    def raising_override(c: object) -> str:
        raise ValueError("override exploded")

    with pytest.raises(ValueError, match="override exploded"):
        embed_document(
            doc_id,
            text,
            db,
            _embedder=_fake_embedder,
            embed_text_override=raising_override,
        )
