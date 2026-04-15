"""Tests for commonplace_worker/handlers/library.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fixtures.library.factory import SAMPLE_AUTHOR, SAMPLE_TITLE, make_epub, make_pdf

from commonplace_db.db import migrate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with all migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def sample_epub(tmp_path: Path) -> Path:
    return make_epub(tmp_path / "sample.epub")


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "sample.pdf")


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    """Return zero-vectors of dimension 768 for each text."""
    return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------
# Epub extraction
# ---------------------------------------------------------------------------


def test_epub_text_extracted(sample_epub: Path, db_conn: sqlite3.Connection) -> None:
    """Text is extracted from an epub and embed_document is called."""
    from commonplace_worker.handlers.library import handle_library_ingest

    result = handle_library_ingest({"path": str(sample_epub)}, db_conn, _embedder=_fake_embedder)

    assert result["document_id"] is not None
    assert result["chunk_count"] is not None and result["chunk_count"] >= 1
    assert result["elapsed_ms"] >= 0


def test_epub_documents_row(sample_epub: Path, db_conn: sqlite3.Connection) -> None:
    """documents row has correct metadata after epub ingest."""
    from commonplace_worker.handlers.library import handle_library_ingest

    result = handle_library_ingest({"path": str(sample_epub)}, db_conn, _embedder=_fake_embedder)

    doc = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "book"
    assert doc["status"] == "embedded"
    assert doc["title"] == SAMPLE_TITLE
    assert doc["author"] == SAMPLE_AUTHOR
    assert doc["content_hash"] is not None
    assert doc["source_uri"] == str(sample_epub)


def test_epub_chunks_and_embeddings(sample_epub: Path, db_conn: sqlite3.Connection) -> None:
    """chunks and embeddings rows are created after epub ingest."""
    from commonplace_worker.handlers.library import handle_library_ingest

    result = handle_library_ingest({"path": str(sample_epub)}, db_conn, _embedder=_fake_embedder)

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


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def test_pdf_documents_row(sample_pdf: Path, db_conn: sqlite3.Connection) -> None:
    """documents row has correct metadata after pdf ingest."""
    from commonplace_worker.handlers.library import handle_library_ingest

    result = handle_library_ingest({"path": str(sample_pdf)}, db_conn, _embedder=_fake_embedder)

    doc = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "book"
    # PDF with blank page — text extraction may yield nothing, so status could vary
    assert doc["status"] in ("embedded",)
    assert doc["title"] == SAMPLE_TITLE


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_same_hash_epub(sample_epub: Path, db_conn: sqlite3.Connection) -> None:
    """Re-ingesting the same file does not create a second documents row."""
    from commonplace_worker.handlers.library import handle_library_ingest

    embed_calls: list[int] = []

    def counting_embedder(texts: list[str], model: str) -> list[list[float]]:
        embed_calls.append(len(texts))
        return [[0.0] * 768 for _ in texts]

    result1 = handle_library_ingest({"path": str(sample_epub)}, db_conn, _embedder=counting_embedder)
    result2 = handle_library_ingest({"path": str(sample_epub)}, db_conn, _embedder=counting_embedder)

    # Same document_id returned
    assert result1["document_id"] == result2["document_id"]

    # Only one documents row
    count = db_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1

    # embed was called only once (idempotency guard in pipeline.embed_document)
    assert len(embed_calls) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_file_raises(db_conn: sqlite3.Connection) -> None:
    from commonplace_worker.handlers.library import handle_library_ingest

    with pytest.raises(FileNotFoundError):
        handle_library_ingest({"path": "/nonexistent/file.epub"}, db_conn)


def test_missing_path_in_payload_raises(db_conn: sqlite3.Connection) -> None:
    from commonplace_worker.handlers.library import handle_library_ingest

    with pytest.raises(ValueError, match="missing 'path'"):
        handle_library_ingest({}, db_conn)


def test_unsupported_format_raises(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    fake = tmp_path / "book.xyz"
    fake.write_bytes(b"fake content")
    from commonplace_worker.handlers.library import handle_library_ingest

    with pytest.raises(ValueError, match="unsupported book format"):
        handle_library_ingest({"path": str(fake)}, db_conn)


def test_chm_skipped(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    fake = tmp_path / "book.chm"
    fake.write_bytes(b"fake chm content")
    from commonplace_worker.handlers.library import handle_library_ingest

    result = handle_library_ingest({"path": str(fake)}, db_conn)
    assert result["skipped"] is True
    assert result["document_id"] is None


def test_mobi_raises_without_calibre(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    """mobi raises RuntimeError when ebook-convert is not on PATH."""
    fake = tmp_path / "book.mobi"
    fake.write_bytes(b"fake mobi content")
    from commonplace_worker.handlers.library import handle_library_ingest

    with patch("shutil.which", return_value=None), pytest.raises(RuntimeError, match="ebook-convert"):
        handle_library_ingest({"path": str(fake)}, db_conn)
