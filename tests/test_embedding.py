"""Unit tests for the Ollama embedding client (Ollama mocked)."""

from __future__ import annotations

import json
import struct
from unittest.mock import patch

import httpx
import pytest

from commonplace_server.embedding import (
    EmbeddingDimensionError,
    embed,
    pack_vector,
    unpack_vector,
)

_DIM = 768


def _fake_response(vector: list[float]) -> httpx.Response:
    """Build a mock httpx.Response carrying *vector*."""
    body = json.dumps({"embedding": vector}).encode()
    request = httpx.Request("POST", "http://localhost:11434/api/embeddings")
    return httpx.Response(200, content=body, request=request)


def _good_vector() -> list[float]:
    return [0.1] * _DIM


# ---------------------------------------------------------------------------
# pack / unpack round-trip
# ---------------------------------------------------------------------------


def test_pack_unpack_roundtrip() -> None:
    original = [float(i) / 1000 for i in range(_DIM)]
    blob = pack_vector(original)
    restored = unpack_vector(blob)
    assert len(restored) == _DIM
    for a, b in zip(original, restored):
        assert abs(a - b) < 1e-6


def test_pack_produces_float32_little_endian() -> None:
    vec = [1.0, 2.0, 3.0]
    blob = pack_vector(vec)
    assert blob == struct.pack("<3f", 1.0, 2.0, 3.0)


def test_unpack_length() -> None:
    blob = struct.pack(f"<{_DIM}f", *([0.0] * _DIM))
    result = unpack_vector(blob)
    assert len(result) == _DIM


# ---------------------------------------------------------------------------
# Successful embed
# ---------------------------------------------------------------------------


def test_embed_returns_correct_dimension() -> None:
    with patch("httpx.post", return_value=_fake_response(_good_vector())) as mock_post:
        result = embed(["hello world"])
    assert len(result) == 1
    assert len(result[0]) == _DIM
    mock_post.assert_called_once()


def test_embed_multiple_texts() -> None:
    with patch("httpx.post", return_value=_fake_response(_good_vector())):
        result = embed(["text one", "text two", "text three"])
    assert len(result) == 3
    for vec in result:
        assert len(vec) == _DIM


def test_embed_passes_correct_model() -> None:
    with patch("httpx.post", return_value=_fake_response(_good_vector())) as mock_post:
        embed(["test"], model="custom-model")
    payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert payload["model"] == "custom-model"


# ---------------------------------------------------------------------------
# Dimension assertion
# ---------------------------------------------------------------------------


def test_wrong_dimension_raises_embedding_dimension_error() -> None:
    bad_vector = [0.0] * 512  # wrong dim
    with patch("httpx.post", return_value=_fake_response(bad_vector)), pytest.raises(
        EmbeddingDimensionError
    ):
        embed(["hello"])


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


def test_transient_failure_then_success_retries() -> None:
    """First call raises HTTPError; second call succeeds."""
    good_resp = _fake_response(_good_vector())
    call_count = 0

    def side_effect(*args: object, **kwargs: object) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused")
        return good_resp

    with patch("httpx.post", side_effect=side_effect):
        result = embed(["hello"])
    assert call_count == 2
    assert len(result[0]) == _DIM


def test_two_consecutive_failures_raises() -> None:
    """Both attempts fail → propagates the last exception."""

    def always_fail(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with patch("httpx.post", side_effect=always_fail), pytest.raises(httpx.ConnectError):
        embed(["hello"])


def test_empty_texts_returns_empty_list() -> None:
    with patch("httpx.post") as mock_post:
        result = embed([])
    assert result == []
    mock_post.assert_not_called()
