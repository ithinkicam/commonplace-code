"""Ollama embedding client.

Wraps the Ollama /api/embeddings endpoint (v0.20.7 API).
Asserts vector dimension == 768 on every response.
Provides pack/unpack helpers for little-endian float32 BLOB storage.
"""

from __future__ import annotations

import struct

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_OLLAMA_BASE = "http://localhost:11434"
_EMBED_URL = f"{_OLLAMA_BASE}/api/embeddings"
_EXPECTED_DIM = 768
_TIMEOUT = 30.0   # seconds per request
_MAX_RETRIES = 1  # one retry on transient HTTP failure


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EmbeddingDimensionError(ValueError):
    """Raised when Ollama returns a vector of unexpected length."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    """Embed a list of texts via Ollama.

    Returns one float list per input text.  Each vector is asserted to be
    exactly 768 dimensions; raises EmbeddingDimensionError otherwise.

    Makes one HTTP POST per text (Ollama v0.20.7 /api/embeddings takes a
    single prompt per request).  Retries once on transient HTTP failure,
    then propagates.
    """
    results: list[list[float]] = []
    for text in texts:
        vector = _embed_one(text, model)
        if len(vector) != _EXPECTED_DIM:
            raise EmbeddingDimensionError(
                f"Expected {_EXPECTED_DIM}-dim vector from Ollama, got {len(vector)}"
            )
        results.append(vector)
    return results


def pack_vector(vec: list[float]) -> bytes:
    """Serialise a float list to a little-endian float32 byte string."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    """Deserialise a little-endian float32 byte string to a float list."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _embed_one(text: str, model: str) -> list[float]:
    """POST to Ollama and return the embedding vector.  Retries once."""
    payload = {"model": model, "prompt": text}
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = httpx.post(_EMBED_URL, json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return list(data["embedding"])
        except (httpx.HTTPError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                continue
            raise

    # Should never reach here, but satisfies type-checker
    assert last_exc is not None
    raise last_exc
