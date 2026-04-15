"""Library book ingest handler.

handle_library_ingest(payload, conn) is the worker handler for the
'ingest_library' job kind.  It extracts text from a book file, inserts a
documents row, and calls pipeline.embed_document() to chunk + embed.

Supported formats: epub, pdf.
mobi / azw3 require calibre's ebook-convert on PATH.
chm is skipped with a log warning.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".epub", ".pdf", ".mobi", ".azw3"}
SKIP_FORMATS = {".chm"}


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_library_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_library' jobs.

    Parameters
    ----------
    payload:
        Must contain ``path`` — absolute path to the book file.
    conn:
        Open SQLite connection with migrations applied.
    _embedder:
        Optional embedder override for tests (passed through to embed_document).

    Returns
    -------
    dict with keys: document_id, chunk_count, elapsed_ms.
    """
    t0 = time.monotonic()

    path_str = payload.get("path")
    if not isinstance(path_str, str) or not path_str:
        raise ValueError(f"ingest_library payload missing 'path': {payload!r}")

    book_path = Path(path_str)
    if not book_path.exists():
        raise FileNotFoundError(f"book file not found: {book_path}")

    suffix = book_path.suffix.lower()
    if suffix in SKIP_FORMATS:
        logger.warning("skipping unsupported format %s: %s", suffix, book_path)
        return {"document_id": None, "chunk_count": 0, "elapsed_ms": 0.0, "skipped": True}

    if suffix not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported book format {suffix!r}: {book_path}")

    # 1. Compute content hash
    content_hash = _sha256(book_path)

    # 2. Idempotency check
    existing = conn.execute(
        "SELECT id FROM documents WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if existing is not None:
        existing_id: int = existing["id"]
        logger.info("book already ingested (content_hash match), document_id=%d", existing_id)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"document_id": existing_id, "chunk_count": None, "elapsed_ms": elapsed_ms, "skipped": True}

    # 3. Extract metadata + text
    try:
        title, author, text = _extract(book_path, suffix)
    except Exception as exc:
        logger.error("extraction failed for %s: %s", book_path, exc)
        # Insert failed document row for observability
        with conn:
            conn.execute(
                """
                INSERT INTO documents
                    (content_type, source_uri, content_hash, raw_path, status)
                VALUES ('book', ?, ?, ?, 'failed')
                """,
                (str(book_path), content_hash, str(book_path)),
            )
        raise

    # 4. Insert documents row
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, author, content_hash, raw_path, status)
            VALUES ('book', ?, ?, ?, ?, ?, 'ingesting')
            """,
            (str(book_path), title, author, content_hash, str(book_path)),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]

    # 5. Chunk + embed via pipeline
    from commonplace_server.pipeline import embed_document

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    result = embed_document(document_id, text, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested document_id=%d chunks=%d elapsed_ms=%.0f path=%s",
        document_id,
        result.chunk_count,
        elapsed_ms,
        book_path,
    )
    return {
        "document_id": document_id,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# Format-specific extraction
# ---------------------------------------------------------------------------


def _extract(path: Path, suffix: str) -> tuple[str | None, str | None, str]:
    """Return (title, author, text) for the given book file."""
    if suffix == ".epub":
        return _extract_epub(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in {".mobi", ".azw3"}:
        return _extract_via_calibre(path)
    raise ValueError(f"no extractor for {suffix!r}")


def _extract_epub(path: Path) -> tuple[str | None, str | None, str]:
    """Extract title, author, and full text from an epub file."""
    import ebooklib  # type: ignore[import-untyped]
    from bs4 import BeautifulSoup
    from ebooklib import epub

    book = epub.read_epub(str(path), options={"ignore_ncx": True})

    # Metadata
    title_meta = book.get_metadata("DC", "title")
    title: str | None = title_meta[0][0] if title_meta else None

    creator_meta = book.get_metadata("DC", "creator")
    author: str | None = creator_meta[0][0] if creator_meta else None

    # Text: concatenate all spine items
    import warnings

    from bs4 import XMLParsedAsHTMLWarning

    parts: list[str] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(item.get_content(), "lxml")
        text = soup.get_text(separator="\n")
        if text.strip():
            parts.append(text.strip())

    return title, author, "\n\n".join(parts)


def _extract_pdf(path: Path) -> tuple[str | None, str | None, str]:
    """Extract title, author, and full text from a PDF file."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    meta = reader.metadata

    title: str | None = None
    author: str | None = None
    if meta:
        title = meta.title or None
        author = meta.author or None

    pages: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(page_text.strip())

    return title, author, "\n\n".join(pages)


def _extract_via_calibre(path: Path) -> tuple[str | None, str | None, str]:
    """Convert mobi/azw3 to epub via ebook-convert, then extract as epub.

    Requires calibre's ebook-convert on PATH. Raises a clear RuntimeError if
    ebook-convert is not available.
    """
    if not shutil.which("ebook-convert"):
        raise RuntimeError(
            f"ebook-convert (calibre) is not on PATH — cannot convert {path.suffix!r} files. "
            "Install calibre from https://calibre-ebook.com/download and ensure "
            "ebook-convert is on your PATH."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        out_epub = Path(tmpdir) / (path.stem + ".epub")
        result = subprocess.run(  # noqa: S603
            ["ebook-convert", str(path), str(out_epub)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ebook-convert failed for {path}: {result.stderr[:500]}"
            )
        return _extract_epub(out_epub)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
