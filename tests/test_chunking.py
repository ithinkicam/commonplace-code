"""Unit tests for the hybrid paragraph / sliding-window chunker."""

from __future__ import annotations

import tiktoken

from commonplace_server.chunking import (
    _CAP_TOKENS,
    _MERGE_FLOOR,
    _WINDOW_SIZE,
    Chunk,
    chunk_text,
)


def _token_count(text: str) -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def _make_tokens(n: int) -> str:
    """Return a string with at least *n* tokens.

    Uses a rotating list of common words whose total token count is measurable.
    Keeps extending until the text reaches at least *n* tokens.
    """
    enc = tiktoken.get_encoding("cl100k_base")
    vocab = [
        "apple", "banana", "cherry", "dragon", "elephant",
        "forest", "garden", "harbor", "island", "jungle",
    ]
    words: list[str] = []
    # Rough estimate: ~1.3 tokens/word on average for these words
    target_words = int(n / 1.3) + 20
    for i in range(target_words):
        words.append(vocab[i % len(vocab)])
    text = " ".join(words)
    # Trim or extend to ensure we reach exactly >= n tokens
    while len(enc.encode(text)) < n:
        words.extend(vocab)
        text = " ".join(words)
    return text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty_list() -> None:
    assert chunk_text("") == []


def test_whitespace_only_returns_empty_list() -> None:
    assert chunk_text("   \n\n  \t  ") == []


def test_short_doc_single_chunk() -> None:
    text = "This is a short document with just one paragraph."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].text == text.strip()
    assert chunks[0].token_count == _token_count(text.strip())


def test_chunk_is_dataclass() -> None:
    chunks = chunk_text("Hello world.")
    assert isinstance(chunks[0], Chunk)
    assert hasattr(chunks[0], "text")
    assert hasattr(chunks[0], "token_count")


def test_multi_paragraph_small_paragraphs_merged() -> None:
    # Two tiny paragraphs should merge into one chunk (both well below 400 tok).
    text = "First paragraph.\n\nSecond paragraph."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert "First paragraph" in chunks[0].text
    assert "Second paragraph" in chunks[0].text


def test_paragraph_chunks_when_each_is_big_enough() -> None:
    # Build two paragraphs each >= MERGE_FLOOR tokens.
    para1 = _make_tokens(_MERGE_FLOOR)
    para2 = _make_tokens(_MERGE_FLOOR)
    text = para1 + "\n\n" + para2
    chunks = chunk_text(text)
    # Each paragraph should be its own chunk.
    assert len(chunks) == 2
    for c in chunks:
        assert c.token_count >= _MERGE_FLOOR


def test_no_chunk_exceeds_cap() -> None:
    # Many paragraphs of ~300 tokens each; none merged chunk should exceed cap.
    parts = [_make_tokens(300) for _ in range(10)]
    text = "\n\n".join(parts)
    chunks = chunk_text(text)
    for c in chunks:
        assert c.token_count <= _CAP_TOKENS


def test_oversized_paragraph_produces_sliding_windows() -> None:
    # Single paragraph > CAP_TOKENS → sliding windows.
    big = _make_tokens(_CAP_TOKENS + 200)
    chunks = chunk_text(big)
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= _WINDOW_SIZE


def test_sliding_window_overlap() -> None:
    # Verify that the token counts reflect expected window progression.
    big = _make_tokens(_CAP_TOKENS + 200)
    chunks = chunk_text(big)
    # All but the last window should be exactly WINDOW_SIZE tokens.
    for c in chunks[:-1]:
        assert c.token_count == _WINDOW_SIZE


def test_mixed_output_normal_and_window_chunks() -> None:
    # Normal para + oversized para → mix of paragraph chunk + window chunks.
    normal = _make_tokens(_MERGE_FLOOR)
    big = _make_tokens(_CAP_TOKENS + 100)
    text = normal + "\n\n" + big
    chunks = chunk_text(text)
    # At least 2 chunks (one normal, at least one window)
    assert len(chunks) >= 2


def test_token_count_matches_actual() -> None:
    text = "The quick brown fox jumped over the lazy dog."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].token_count == _token_count(text.strip())


def test_deterministic() -> None:
    text = "Alpha.\n\nBeta.\n\nGamma."
    assert chunk_text(text) == chunk_text(text)
