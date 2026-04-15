"""Hybrid paragraph / sliding-window text chunker.

ADR-0005 §1: split on paragraph boundaries first; merge short paragraphs until
each chunk reaches ~400 tokens; cap at 1500 tokens.  When a single paragraph
exceeds 1500 tokens, emit sliding windows of 512 tokens with 64-token overlap.

All functions are pure and deterministic — no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

# ---------------------------------------------------------------------------
# Constants (per ADR-0005 §1)
# ---------------------------------------------------------------------------
_MERGE_FLOOR = 400      # merge adjacent paragraphs until chunk >= this
_CAP_TOKENS = 1500      # hard ceiling per paragraph-chunk
_WINDOW_SIZE = 512      # sliding-window size for overlong paragraphs
_WINDOW_OVERLAP = 64    # overlap between consecutive windows

_ENCODING_NAME = "cl100k_base"


def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding(_ENCODING_NAME)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    text: str
    token_count: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_text(text: str) -> list[Chunk]:
    """Split *text* into chunks suitable for embedding.

    Returns an empty list for empty / whitespace-only input.
    The output is deterministic: same input always produces the same chunks.
    """
    if not text or not text.strip():
        return []

    enc = _encoder()
    paragraphs = _split_paragraphs(text)
    chunks: list[Chunk] = []

    for para in paragraphs:
        token_ids = enc.encode(para)
        n_tokens = len(token_ids)

        if n_tokens > _CAP_TOKENS:
            # Overlong paragraph → sliding windows
            chunks.extend(_sliding_windows(para, token_ids, enc))
        else:
            # Normal paragraph — accumulate into the last chunk if still short
            if chunks and chunks[-1].token_count < _MERGE_FLOOR:
                merged_text = chunks[-1].text + "\n\n" + para
                merged_ids = enc.encode(merged_text)
                merged_n = len(merged_ids)
                if merged_n <= _CAP_TOKENS:
                    chunks[-1] = Chunk(text=merged_text, token_count=merged_n)
                    continue
            # Either no previous chunk, previous chunk is already big enough,
            # or merging would exceed the cap — start a new chunk.
            chunks.append(Chunk(text=para, token_count=n_tokens))

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> list[str]:
    """Split on two-or-more newlines; strip each paragraph."""
    parts = re.split(r"\n{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def _sliding_windows(
    para: str,
    token_ids: list[int],
    enc: tiktoken.Encoding,
) -> list[Chunk]:
    """Emit fixed-size windows over *token_ids* with overlap."""
    windows: list[Chunk] = []
    step = _WINDOW_SIZE - _WINDOW_OVERLAP
    start = 0
    total = len(token_ids)

    while start < total:
        end = min(start + _WINDOW_SIZE, total)
        window_ids = token_ids[start:end]
        window_text = enc.decode(window_ids)
        windows.append(Chunk(text=window_text, token_count=len(window_ids)))
        if end >= total:
            break
        start += step

    return windows
