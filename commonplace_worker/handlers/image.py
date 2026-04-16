"""Image / screenshot ingest handler.

``handle_image_ingest(payload, conn)`` is the worker handler for
``ingest_image`` jobs.  When a capture arrives with ``kind="image"`` the
handler:

1. Loads the image from a file path, base64-encoded data, or URL.
2. Runs Tesseract OCR to extract text.
3. Preserves the original image in the vault under
   ``captures/YYYY/MM/images/<timestamp>-<hash8>.<ext>``.
4. Writes a capture markdown file with YAML frontmatter + OCR text body.
5. Optionally invokes the ``summarize_capture`` skill for long OCR output.
6. Enqueues an embedding job via ``pipeline.embed_document``.

Typed exceptions
----------------
- :class:`ImageInputError` -- missing/invalid input fields.
- :class:`UnsupportedImageFormat` -- format not in the supported set.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from PIL import Image

logger = logging.getLogger(__name__)

# Supported PIL format names -> canonical file extension
_FORMAT_TO_EXT: dict[str, str] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "TIFF": ".tiff",
    "BMP": ".bmp",
    "GIF": ".gif",
}

# Minimum OCR chars to consider the OCR "non-empty"
_OCR_EMPTY_THRESHOLD = 50


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class ImageError(Exception):
    """Base class for image handler errors."""


class ImageInputError(ImageError):
    """Payload is missing required fields or the image cannot be loaded."""


class UnsupportedImageFormat(ImageError):
    """Image format is not in the supported set."""


# ---------------------------------------------------------------------------
# Type aliases for injectable seams
# ---------------------------------------------------------------------------

OcrFn = Callable[[Image.Image], str]
SummarizerFn = Callable[[str], str | None]
FetchUrlFn = Callable[[str], bytes]


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_image_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _ocr: OcrFn | None = None,
    _summarizer: SummarizerFn | None = None,
    _embedder: Any = None,
    _fetch_url: FetchUrlFn | None = None,
) -> dict[str, Any]:
    """Worker handler for ``ingest_image`` jobs.

    Parameters
    ----------
    payload:
        ``{"image_path": str|None, "image_data": str|None, "url": str|None,
          "inbox_file": str|None}``.
        At least one of ``image_path``, ``image_data``, ``url`` must be present.
    conn:
        Open SQLite connection with migrations applied.
    _ocr:
        Optional OCR function override for tests. Signature: ``(PIL.Image) -> str``.
    _summarizer:
        Optional summarizer override for tests. ``(text) -> summary_text | None``.
    _embedder:
        Optional embedder override forwarded to ``pipeline.embed_document``.
    _fetch_url:
        Optional URL fetcher override for tests. ``(url) -> bytes``.

    Returns
    -------
    dict with keys: ``document_id``, ``chunk_count``, ``elapsed_ms``,
    ``ocr_chars``, ``ocr_empty``, ``image_preserved_path``, ``summarized``.
    """
    t0 = time.monotonic()

    # 1. Load image bytes from one of the three input modes.
    image_bytes, original_filename = _load_image_bytes(payload, _fetch_url)

    # 2. Validate format via Pillow.
    img, fmt = _open_and_validate(image_bytes)

    # 3. Content hash for idempotency.
    content_hash = hashlib.sha256(image_bytes).hexdigest()

    # 4. Idempotency check.
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
        logger.info("image already ingested document_id=%d", existing_id)
        return {
            "document_id": existing_id,
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "ocr_chars": 0,
            "ocr_empty": True,
            "image_preserved_path": "",
            "summarized": False,
        }

    # 5. OCR
    ocr_fn: OcrFn = _ocr if _ocr is not None else _default_ocr
    ocr_text = ocr_fn(img).strip()
    ocr_chars = len(ocr_text)
    ocr_empty = ocr_chars < _OCR_EMPTY_THRESHOLD

    # 6. Preserve original image in vault.
    captured_at = datetime.now(UTC)
    hash8 = content_hash[:8]
    ext = _FORMAT_TO_EXT[fmt]
    image_preserved_path = _preserve_image(
        image_bytes=image_bytes,
        captured_at=captured_at,
        hash8=hash8,
        ext=ext,
    )

    # 7. Optional summarization for long OCR text.
    summarized = False
    from skills.summarize_capture.parser import should_summarize

    if ocr_text and should_summarize(ocr_text) and _summarizer is not None:
        summary = _summarizer(ocr_text)
        if summary:
            summarized = True

    # 8. Write vault markdown file.
    vault_root = _vault_root()
    image_rel = str(image_preserved_path.relative_to(vault_root))
    md_path = _write_vault_markdown(
        captured_at=captured_at,
        hash8=hash8,
        image_rel=image_rel,
        ocr_text=ocr_text,
        ocr_chars=ocr_chars,
        ocr_empty=ocr_empty,
        content_hash=content_hash,
        original_filename=original_filename,
        summarized=summarized,
    )

    # 9. Insert documents row.
    embed_text = ocr_text if ocr_text else f"[image: {original_filename or 'untitled'}]"
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, content_hash,
                 raw_path, source_id, status)
            VALUES ('image', ?, ?, ?, ?, ?, 'ingesting')
            """,
            (
                image_rel,
                original_filename or f"image-{hash8}",
                content_hash,
                str(md_path),
                content_hash,
            ),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]

    # 10. Chunk + embed via pipeline.
    from commonplace_server.pipeline import embed_document

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    result = embed_document(document_id, embed_text, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested image document_id=%d ocr_chars=%d chunks=%d elapsed_ms=%.0f",
        document_id,
        ocr_chars,
        result.chunk_count,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "ocr_chars": ocr_chars,
        "ocr_empty": ocr_empty,
        "image_preserved_path": str(image_preserved_path),
        "summarized": summarized,
    }


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def _load_image_bytes(
    payload: dict[str, Any],
    fetch_url: FetchUrlFn | None = None,
) -> tuple[bytes, str | None]:
    """Load image bytes from the payload. Returns (bytes, original_filename)."""
    image_path = payload.get("image_path")
    image_data = payload.get("image_data")
    url = payload.get("url")

    if not image_path and not image_data and not url:
        raise ImageInputError(
            "payload must contain at least one of 'image_path', 'image_data', 'url'"
        )

    if image_path:
        p = Path(image_path)
        if not p.exists():
            raise ImageInputError(f"image file not found: {image_path}")
        return p.read_bytes(), p.name

    if image_data:
        try:
            raw = base64.b64decode(image_data)
        except Exception as exc:
            raise ImageInputError(f"invalid base64 in 'image_data': {exc}") from exc
        return raw, None

    # URL mode
    assert url is not None
    fetcher = fetch_url if fetch_url is not None else _default_fetch_url
    raw = fetcher(url)
    # Try to extract a filename from the URL
    from urllib.parse import urlparse

    parsed = urlparse(url)
    name = Path(parsed.path).name if parsed.path else None
    return raw, name


def _default_fetch_url(url: str) -> bytes:
    """Fetch image bytes from a URL."""
    try:
        with urlopen(url, timeout=30) as resp:  # noqa: S310
            return resp.read()  # type: ignore[no-any-return]
    except Exception as exc:
        raise ImageInputError(f"failed to fetch image from URL {url!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Format validation
# ---------------------------------------------------------------------------


def _open_and_validate(image_bytes: bytes) -> tuple[Image.Image, str]:
    """Open with Pillow and validate the format. Returns (image, format_name)."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()  # Force full decode to catch corrupt files early
    except Exception as exc:
        raise ImageInputError(f"cannot open image: {exc}") from exc

    fmt = img.format
    if fmt is None or fmt not in _FORMAT_TO_EXT:
        raise UnsupportedImageFormat(
            f"unsupported image format: {fmt!r}. "
            f"Supported: {', '.join(sorted(_FORMAT_TO_EXT.keys()))}"
        )
    return img, fmt


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def _default_ocr(img: Image.Image) -> str:
    """Run Tesseract OCR on a PIL Image."""
    import pytesseract  # type: ignore[import-untyped]

    return pytesseract.image_to_string(img)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Vault writing
# ---------------------------------------------------------------------------


def _vault_root() -> Path:
    root = os.environ.get("COMMONPLACE_VAULT_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / "commonplace"


def _preserve_image(
    *,
    image_bytes: bytes,
    captured_at: datetime,
    hash8: str,
    ext: str,
) -> Path:
    """Atomically write the original image to the vault images directory."""
    vault_root = _vault_root()
    year = captured_at.strftime("%Y")
    month = captured_at.strftime("%m")
    out_dir = vault_root / "captures" / year / month / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = captured_at.strftime("%Y-%m-%dT%H%M%SZ")
    filename = f"{ts}-{hash8}{ext}"
    final_path = out_dir / filename
    tmp_path = out_dir / f"{filename}.tmp"

    with tmp_path.open("wb") as fh:
        fh.write(image_bytes)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(final_path)
    return final_path


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _yaml_escape(value: str) -> str:
    """Minimal YAML-safe escaping for a single-line scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_vault_markdown(
    *,
    captured_at: datetime,
    hash8: str,
    image_rel: str,
    ocr_text: str,
    ocr_chars: int,
    ocr_empty: bool,
    content_hash: str,
    original_filename: str | None,
    summarized: bool,
) -> Path:
    """Write the capture markdown file and return its path."""
    vault_root = _vault_root()
    year = captured_at.strftime("%Y")
    month = captured_at.strftime("%m")
    out_dir = vault_root / "captures" / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = captured_at.strftime("%Y-%m-%dT%H%M%SZ")
    filename = f"{ts}-image-{hash8}.md"
    final_path = out_dir / filename
    tmp_path = out_dir / f"{filename}.tmp"

    lines: list[str] = ["---", "source: image"]
    lines.append(f"image_path: {_yaml_escape(image_rel)}")
    lines.append(f"ocr_chars: {ocr_chars}")
    if ocr_empty:
        lines.append("ocr_empty: true")
    lines.append(f"content_hash: {_yaml_escape(content_hash)}")
    if original_filename:
        lines.append(f"original_filename: {_yaml_escape(original_filename)}")
    lines.append(
        f"captured_at: {_yaml_escape(captured_at.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
    )
    lines.append(f"summarized: {'true' if summarized else 'false'}")
    lines.append("---")
    lines.append("")
    if ocr_text:
        lines.append(ocr_text.rstrip() + "\n")
    else:
        lines.append("")

    content = "\n".join(lines)

    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(final_path)
    return final_path
