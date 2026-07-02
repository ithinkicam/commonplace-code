#!/usr/bin/env python3
"""Re-embed documents with the title/metadata header prepended.

Useful one-shot after the embed-header fix landed: documents that were
ingested before the handler started prepending a ``Title:`` / ``URL:``
header need their chunks regenerated, or semantic search on the title
won't hit them.

Usage
-----
    python scripts/reembed_documents.py --ids 9241,9244,9245 [--dry-run]

Exit codes
----------
  0  all docs re-embedded successfully (or dry-run completed)
  1  one or more docs failed
  2  bad arguments

The script:
  1. Reads the document row (title, source_id as URL, content_type).
  2. Concatenates existing chunks to reconstruct the body text.
  3. Renders the new embed header using
     ``commonplace_worker.frontmatter.render_embed_header`` with
     content-type-appropriate field labels.
  4. DELETEs existing chunks (CASCADE deletes embeddings).
  5. Re-runs ``commonplace_server.pipeline.embed_document`` with
     ``header + body``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("reembed_documents")


_HEADER_LABELS: frozenset[str] = frozenset({
    "title", "episode", "show", "channel", "url", "author",
    "source", "uploaded", "published", "filename", "captured",
})


def _strip_existing_header(text: str) -> str:
    """Remove a leading ``Label: value`` block from reconstructed body text.

    The runtime handlers prepend a short ``render_embed_header`` block,
    which means a document ingested after that feature landed already
    has its header baked into chunk 0. Re-embedding would otherwise
    stack a fresh header on top of the old one and store both in the
    new chunk 0. Detect lines that look like ``<Label>: <rest>``
    (where Label is one of the known header labels) at the top of the
    text and drop them along with the one blank line that follows.
    """
    lines = text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            break
        label, sep, _ = stripped.partition(":")
        if not sep or label.lower() not in _HEADER_LABELS:
            return text
        idx += 1
    if idx == 0:
        return text
    # Drop the trailing blank line too (one blank separates header from body).
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    return "".join(lines[idx:])


def _header_for(content_type: str, title: str | None, url: str | None) -> str:
    from commonplace_worker.frontmatter import render_embed_header

    # Use labels matching the handler that produced each content_type so
    # re-embedded docs look identical to freshly-ingested ones.
    if content_type == "youtube":
        return render_embed_header(
            [("Title", title), ("URL", url)]
        )
    if content_type == "podcast":
        return render_embed_header(
            [("Episode", title), ("URL", url)]
        )
    if content_type == "article":
        return render_embed_header(
            [("Title", title), ("URL", url)]
        )
    # Generic fallback — still better than nothing for unusual types.
    return render_embed_header([("Title", title), ("URL", url)])


def reembed_one(conn, document_id: int, *, dry_run: bool) -> bool:
    """Re-embed a single document. Returns True on success."""
    row = conn.execute(
        "SELECT id, title, source_id, content_type FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        logger.error("document_id=%d not found", document_id)
        return False
    title = row["title"]
    url = row["source_id"]
    content_type = row["content_type"]

    chunks = conn.execute(
        "SELECT text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
        (document_id,),
    ).fetchall()
    if not chunks:
        logger.error(
            "document_id=%d has no existing chunks — nothing to reconstruct",
            document_id,
        )
        return False

    raw_body = "\n\n".join(c["text"] for c in chunks)
    body = _strip_existing_header(raw_body)
    header = _header_for(content_type, title, url)
    new_text = header + body

    logger.info(
        "document_id=%d title=%r url=%s chunks=%d body_chars=%d header=%r",
        document_id, title, url, len(chunks), len(body),
        header.strip().replace("\n", " | "),
    )

    if dry_run:
        return True

    # Delete existing chunks; FK cascade removes `embeddings` rows. The
    # sqlite-vec `chunk_vectors` virtual table does NOT participate in
    # foreign-key cascades, so we explicitly delete its rows by joining
    # back through the chunks table BEFORE the chunks rows disappear.
    with conn:
        chunk_ids = [
            int(r["id"])
            for r in conn.execute(
                "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
            ).fetchall()
        ]
        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            conn.execute(
                f"DELETE FROM chunk_vectors WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))

    # Re-embed via the production pipeline.
    from commonplace_server.pipeline import embed_document

    try:
        result = embed_document(document_id, new_text, conn)
    except Exception as exc:
        logger.error("embed_document failed for %d: %s", document_id, exc)
        return False
    logger.info(
        "re-embedded document_id=%d new_chunks=%d tokens=%d elapsed_ms=%.0f",
        document_id, result.chunk_count, result.total_tokens, result.elapsed_ms,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ids",
        required=True,
        help="Comma-separated document ids to re-embed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be re-embedded without writing anything",
    )
    args = parser.parse_args(argv)

    try:
        ids = [int(x.strip()) for x in args.ids.split(",") if x.strip()]
    except ValueError as exc:
        logger.error("--ids must be a comma-separated list of integers: %s", exc)
        return 2
    if not ids:
        logger.error("--ids must supply at least one id")
        return 2

    from commonplace_db.db import connect

    conn = connect()

    failures = 0
    for doc_id in ids:
        ok = reembed_one(conn, doc_id, dry_run=args.dry_run)
        if not ok:
            failures += 1

    if failures:
        logger.error("%d/%d re-embeds failed", failures, len(ids))
        return 1
    logger.info("all %d docs %s successfully",
                len(ids), "inspected" if args.dry_run else "re-embedded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
