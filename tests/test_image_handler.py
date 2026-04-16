"""Tests for commonplace_worker/handlers/image.py."""

from __future__ import annotations

import base64
import io
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

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


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(root))
    return root


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[0.0] * 768 for _ in texts]


def _make_test_png(text: str = "hello world") -> bytes:
    """Create a small PNG image in memory."""
    img = Image.new("RGB", (200, 50), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_test_image(fmt: str = "PNG", ext: str = ".png") -> bytes:
    """Create a small image in the given format."""
    img = Image.new("RGB", (100, 50), color=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _fake_ocr(img: Image.Image) -> str:
    return "This is the OCR-extracted text from the test image for Commonplace."


def _fake_ocr_empty(img: Image.Image) -> str:
    return "hi"


# ---------------------------------------------------------------------------
# 1. Happy path with mocked OCR returning known text
# ---------------------------------------------------------------------------


def test_happy_path_file_input(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "screenshot.png"
    img_file.write_bytes(img_bytes)

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
    )

    assert result["document_id"] is not None
    assert result["chunk_count"] >= 1
    assert result["ocr_chars"] == len(_fake_ocr(Image.new("RGB", (1, 1))))
    assert result["ocr_empty"] is False
    assert result["summarized"] is False
    assert result["elapsed_ms"] >= 0
    assert result["image_preserved_path"] != ""

    # Verify document in DB
    doc = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "image"
    assert doc["status"] == "embedded"


# ---------------------------------------------------------------------------
# 2. base64-encoded image input
# ---------------------------------------------------------------------------


def test_base64_input(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    b64 = base64.b64encode(img_bytes).decode("ascii")

    result = handle_image_ingest(
        {"image_data": b64},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
    )

    assert result["document_id"] is not None
    assert result["ocr_empty"] is False


# ---------------------------------------------------------------------------
# 3. file-path image input (already tested above, additional edge case)
# ---------------------------------------------------------------------------


def test_file_path_not_found(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.image import ImageInputError, handle_image_ingest

    with pytest.raises(ImageInputError, match="not found"):
        handle_image_ingest(
            {"image_path": "/nonexistent/image.png"},
            db_conn,
            _ocr=_fake_ocr,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# 4. URL image input (mocked fetch)
# ---------------------------------------------------------------------------


def test_url_input(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()

    def fake_fetch(url: str) -> bytes:
        return img_bytes

    result = handle_image_ingest(
        {"url": "https://example.com/screenshot.png"},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
        _fetch_url=fake_fetch,
    )

    assert result["document_id"] is not None
    assert result["ocr_empty"] is False


# ---------------------------------------------------------------------------
# 5. Empty OCR result (< 50 chars) succeeds with ocr_empty=True
# ---------------------------------------------------------------------------


def test_empty_ocr_succeeds(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "blank.png"
    img_file.write_bytes(img_bytes)

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr_empty,
        _embedder=_fake_embedder,
    )

    assert result["document_id"] is not None
    assert result["ocr_empty"] is True
    assert result["ocr_chars"] < 50

    # Image is still preserved
    assert Path(result["image_preserved_path"]).exists()


# ---------------------------------------------------------------------------
# 6. Idempotency (same image bytes -> same document_id)
# ---------------------------------------------------------------------------


def test_idempotency(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "dup.png"
    img_file.write_bytes(img_bytes)

    r1 = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
    )
    r2 = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
    )

    assert r1["document_id"] == r2["document_id"]
    count = db_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# 7. Unsupported format rejected
# ---------------------------------------------------------------------------


def test_unsupported_format_rejected(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import (
        UnsupportedImageFormat,
        handle_image_ingest,
    )

    # Create an ICO file (not in supported set)
    img = Image.new("RGB", (16, 16), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="ICO")
    ico_bytes = buf.getvalue()

    ico_file = tmp_path / "icon.ico"
    ico_file.write_bytes(ico_bytes)

    with pytest.raises(UnsupportedImageFormat):
        handle_image_ingest(
            {"image_path": str(ico_file)},
            db_conn,
            _ocr=_fake_ocr,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# 8. Missing all input fields -> typed exception
# ---------------------------------------------------------------------------


def test_missing_all_inputs(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.image import ImageInputError, handle_image_ingest

    with pytest.raises(ImageInputError, match="at least one"):
        handle_image_ingest(
            {},
            db_conn,
            _ocr=_fake_ocr,
            _embedder=_fake_embedder,
        )

    with pytest.raises(ImageInputError, match="at least one"):
        handle_image_ingest(
            {"image_path": None, "image_data": None, "url": None},
            db_conn,
            _ocr=_fake_ocr,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# 9. Vault file format validated (frontmatter + body)
# ---------------------------------------------------------------------------


def test_vault_file_format(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "doc_screenshot.png"
    img_file.write_bytes(img_bytes)

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
    )

    # Find the markdown file via the documents table
    doc = db_conn.execute(
        "SELECT raw_path FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    md_path = Path(doc["raw_path"])
    assert md_path.exists()

    text = md_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")

    # Parse frontmatter
    closing_idx = text.index("\n---\n", 4)
    frontmatter = text[4:closing_idx]
    body = text[closing_idx + len("\n---\n") :]

    assert "source: image" in frontmatter
    assert "image_path:" in frontmatter
    assert "ocr_chars:" in frontmatter
    assert "content_hash:" in frontmatter
    assert "captured_at:" in frontmatter
    assert "summarized:" in frontmatter
    assert 'original_filename: "doc_screenshot.png"' in frontmatter

    # Body contains the OCR text
    assert "OCR-extracted text" in body

    # Directory layout
    assert md_path.parent.parent.parent.name == "captures"
    assert len(md_path.parent.parent.name) == 4  # year
    assert len(md_path.parent.name) == 2  # month

    # No tmp files left behind
    leftovers = list(md_path.parent.glob("*.tmp"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# 10. Image file preserved at expected path
# ---------------------------------------------------------------------------


def test_image_preserved(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "preserve_test.png"
    img_file.write_bytes(img_bytes)

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
    )

    preserved = Path(result["image_preserved_path"])
    assert preserved.exists()
    assert preserved.suffix == ".png"
    assert preserved.parent.name == "images"
    # The preserved image has the same bytes as the original
    assert preserved.read_bytes() == img_bytes


# ---------------------------------------------------------------------------
# 11. JPEG format support
# ---------------------------------------------------------------------------


def test_jpeg_format(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_image("JPEG", ".jpg")
    img_file = tmp_path / "photo.jpg"
    img_file.write_bytes(img_bytes)

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr,
        _embedder=_fake_embedder,
    )

    assert result["document_id"] is not None
    preserved = Path(result["image_preserved_path"])
    assert preserved.suffix == ".jpg"


# ---------------------------------------------------------------------------
# 12. Invalid base64 raises typed exception
# ---------------------------------------------------------------------------


def test_invalid_base64(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    from commonplace_worker.handlers.image import ImageInputError, handle_image_ingest

    with pytest.raises(ImageInputError, match="invalid base64"):
        handle_image_ingest(
            {"image_data": "not-valid-base64!!!"},
            db_conn,
            _ocr=_fake_ocr,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# 13. Summarizer invoked for long OCR text
# ---------------------------------------------------------------------------


def test_summarizer_invoked_for_long_text(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "long_doc.png"
    img_file.write_bytes(img_bytes)

    long_text = " ".join(["word"] * 2500)
    summarizer_calls: list[str] = []

    def long_ocr(img: Image.Image) -> str:
        return long_text

    def fake_summarizer(text: str) -> str | None:
        summarizer_calls.append(text)
        return "A summary."

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=long_ocr,
        _summarizer=fake_summarizer,
        _embedder=_fake_embedder,
    )

    assert result["summarized"] is True
    assert len(summarizer_calls) == 1


# ---------------------------------------------------------------------------
# 14. Summarizer NOT invoked for short OCR text
# ---------------------------------------------------------------------------


def test_summarizer_not_invoked_for_short_text(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "short.png"
    img_file.write_bytes(img_bytes)

    summarizer_calls: list[str] = []

    def fake_summarizer(text: str) -> str | None:
        summarizer_calls.append(text)
        return "A summary."

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr,
        _summarizer=fake_summarizer,
        _embedder=_fake_embedder,
    )

    assert result["summarized"] is False
    assert len(summarizer_calls) == 0


# ---------------------------------------------------------------------------
# 15. Empty OCR vault markdown has ocr_empty flag
# ---------------------------------------------------------------------------


def test_empty_ocr_vault_frontmatter(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    from commonplace_worker.handlers.image import handle_image_ingest

    img_bytes = _make_test_png()
    img_file = tmp_path / "empty_ocr.png"
    img_file.write_bytes(img_bytes)

    result = handle_image_ingest(
        {"image_path": str(img_file)},
        db_conn,
        _ocr=_fake_ocr_empty,
        _embedder=_fake_embedder,
    )

    doc = db_conn.execute(
        "SELECT raw_path FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    md_path = Path(doc["raw_path"])
    text = md_path.read_text(encoding="utf-8")
    assert "ocr_empty: true" in text
