"""Embedding pipeline glue.

embed_document() is the single entry-point that handlers call.  It:
  1. Chunks the raw text.
  2. Embeds each chunk via Ollama.
  3. Inserts chunk rows (with token_count) into `chunks`.
  4. Inserts embedding rows into `embeddings`.
  5. Inserts vector rows into the sqlite-vec `chunk_vectors` virtual table.
  6. Marks documents.status = 'embedded'.

The operation is idempotent: if chunks already exist for the document the
function returns immediately without modifying the database.

Optional *embed_text_override*: a ``Callable[[Chunk], str]`` that composes the
string sent to the embedder for each chunk.  ``chunks.text`` always stores the
raw display text regardless of this override.  When absent (the default for all
existing callers) the embedder receives ``chunk.text`` exactly as today.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from commonplace_server.chunking import Chunk

# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


@dataclass
class EmbedResult:
    chunk_count: int
    total_tokens: int
    model: str
    elapsed_ms: float


# ---------------------------------------------------------------------------
# Type alias for a pluggable embedder (enables testing without Ollama)
# ---------------------------------------------------------------------------
EmbedFn = Callable[[list[str], str], list[list[float]]]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed_document(
    document_id: int,
    text: str,
    conn: sqlite3.Connection,
    model: str = "nomic-embed-text",
    *,
    _embedder: EmbedFn | None = None,
    embed_text_override: Callable[[Chunk], str] | None = None,
) -> EmbedResult:
    """Chunk, embed, and store vectors for *document_id*.

    *conn* must already have migrations applied and sqlite-vec loaded.

    *_embedder* is an internal seam for tests; callers should leave it None
    so the real Ollama embedder is used.

    *embed_text_override* is an optional callable that composes the string sent
    to the embedder for each chunk.  When provided, the embedder receives
    ``[embed_text_override(chunk) for chunk in chunks]``.  When absent (the
    default), the embedder receives ``[chunk.text for chunk in chunks]``.
    The text stored in the ``chunks`` table is always ``chunk.text``
    regardless of this override.

    Returns an EmbedResult summary.  If chunks already exist for the document
    (idempotency guard) the function returns immediately with the stored counts.
    """
    from commonplace_server.chunking import chunk_text
    from commonplace_server.embedding import embed, pack_vector

    embedder: EmbedFn = _embedder if _embedder is not None else embed

    t0 = time.monotonic()

    with conn:
        # Idempotency guard — if any chunk exists, we already ran.
        existing = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document_id,)
        ).fetchone()[0]

        if existing > 0:
            # Return stored summary without re-embedding.
            stored_count = existing
            total_tok = conn.execute(
                "SELECT COALESCE(SUM(token_count), 0) FROM chunks WHERE document_id = ?",
                (document_id,),
            ).fetchone()[0]
            elapsed = (time.monotonic() - t0) * 1000
            return EmbedResult(
                chunk_count=stored_count,
                total_tokens=int(total_tok),
                model=model,
                elapsed_ms=elapsed,
            )

        # 1. Chunk
        chunks = chunk_text(text)

        if not chunks:
            elapsed = (time.monotonic() - t0) * 1000
            conn.execute(
                "UPDATE documents SET status = 'embedded', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                (document_id,),
            )
            return EmbedResult(chunk_count=0, total_tokens=0, model=model, elapsed_ms=elapsed)

        # 2. Embed
        if embed_text_override is not None:
            texts = [embed_text_override(c) for c in chunks]
        else:
            texts = [c.text for c in chunks]
        vectors = embedder(texts, model)

        # 3–5. Insert chunks, embeddings, vec rows
        for idx, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
            cursor = conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text, token_count) VALUES (?, ?, ?, ?)",
                (document_id, idx, chunk.text, chunk.token_count),
            )
            chunk_id = cursor.lastrowid

            blob = pack_vector(vec)
            conn.execute(
                "INSERT INTO embeddings (chunk_id, model, vector_blob) VALUES (?, ?, ?)",
                (chunk_id, model, blob),
            )

            conn.execute(
                "INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, blob),
            )

        # 6. Mark document embedded
        conn.execute(
            "UPDATE documents SET status = 'embedded', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (document_id,),
        )

    elapsed = (time.monotonic() - t0) * 1000
    total_tokens = sum(c.token_count for c in chunks)
    return EmbedResult(
        chunk_count=len(chunks),
        total_tokens=total_tokens,
        model=model,
        elapsed_ms=elapsed,
    )
