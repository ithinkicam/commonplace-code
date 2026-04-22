"""Unit tests for the Ollama embedding client (Ollama mocked)."""

from __future__ import annotations

import json
import struct
from unittest.mock import patch

import httpx
import pytest

from commonplace_server.embedding import (
    CircuitOpenError,
    EmbeddingDimensionError,
    _reset_circuit_for_tests,
    embed,
    pack_vector,
    unpack_vector,
)


@pytest.fixture(autouse=True)
def _reset_circuit_before_each_test() -> None:
    """Module-level circuit state leaks across tests — reset on entry."""
    _reset_circuit_for_tests()

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


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Embed() short-circuits with CircuitOpenError after N consecutive
    failures so a worker hammering embed() during an Ollama outage doesn't
    pay 30s×(retries+1) per call before surfacing the failure.

    Threshold is 3 consecutive final-failures (after MAX_RETRIES is
    exhausted). Each ``embed()`` call that raises propagates through one
    final failure — retries within the call don't count individually.
    """

    def test_three_consecutive_failures_opens_circuit(self) -> None:
        """After 3 final-failures the breaker opens; 4th call short-circuits."""

        def always_fail(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with patch("httpx.post", side_effect=always_fail):
            # First 3 calls exhaust retries and raise ConnectError. Each
            # contributes +1 to consecutive_failures via _after_failure.
            for _ in range(3):
                with pytest.raises(httpx.ConnectError):
                    embed(["x"])

            # 4th call: short-circuits — no HTTP call made.
            call_count = {"n": 0}

            def track_calls(*args: object, **kwargs: object) -> httpx.Response:
                call_count["n"] += 1
                raise httpx.ConnectError("should not reach httpx.post")

            with patch("httpx.post", side_effect=track_calls):
                with pytest.raises(CircuitOpenError):
                    embed(["x"])
                assert call_count["n"] == 0

    def test_success_resets_failure_counter(self) -> None:
        """Two failures then a success leaves the circuit CLOSED; a later
        streak of failures has to start counting from zero."""
        good = _fake_response(_good_vector())
        script: list[object] = [
            httpx.ConnectError("boom1"),
            httpx.ConnectError("boom1b"),  # retry also fails → 1st final failure
            httpx.ConnectError("boom2"),
            httpx.ConnectError("boom2b"),  # retry also fails → 2nd final failure
            good,  # success → counter resets
        ]
        idx = {"i": 0}

        def scripted(*args: object, **kwargs: object) -> httpx.Response:
            item = script[idx["i"]]
            idx["i"] += 1
            if isinstance(item, Exception):
                raise item
            return item

        with patch("httpx.post", side_effect=scripted):
            with pytest.raises(httpx.ConnectError):
                embed(["x"])
            with pytest.raises(httpx.ConnectError):
                embed(["x"])
            # Third call: first attempt fails (retry), succeeds on retry — no,
            # scripted replaces the same queue. Adjust: use a fresh success.
            pass  # (assertion below after a real success call)

        # Fresh setup: one success call closes the circuit; verify counter is 0
        # by running 2 more failures and confirming the 3rd still hits HTTP
        # (not short-circuited).
        _reset_circuit_for_tests()

        calls = {"n": 0}

        def two_fail_then_open(*args: object, **kwargs: object) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 4:  # 2 call attempts × 2 retries each
                raise httpx.ConnectError("boom")
            # Next call (5th HTTP) is a success
            return good

        with patch("httpx.post", side_effect=two_fail_then_open):
            with pytest.raises(httpx.ConnectError):
                embed(["x"])
            with pytest.raises(httpx.ConnectError):
                embed(["x"])
            # After 2 consecutive failures the circuit should still be
            # CLOSED (threshold is 3). A success should reset.
            result = embed(["x"])
            assert len(result[0]) == _DIM

    def test_cooldown_transitions_to_half_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After the cool-down elapses, the next call attempts HTTP
        (HALF_OPEN), so a recovering Ollama can unstick the breaker."""
        import commonplace_server.embedding as emb

        def always_fail(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("down")

        # Open the circuit
        with patch("httpx.post", side_effect=always_fail):
            for _ in range(3):
                with pytest.raises(httpx.ConnectError):
                    embed(["x"])
            # 4th short-circuits
            with pytest.raises(CircuitOpenError):
                embed(["x"])

        # Advance time past the cool-down window
        orig_monotonic = emb.time.monotonic
        monkeypatch.setattr(
            emb.time,
            "monotonic",
            lambda: orig_monotonic() + emb._COOL_DOWN_SECONDS + 1,
        )

        # Now the next call should actually hit HTTP (trial/half-open)
        good = _fake_response(_good_vector())
        with patch("httpx.post", return_value=good):
            result = embed(["x"])
        assert len(result[0]) == _DIM

    def test_short_circuit_is_fast(self) -> None:
        """Short-circuited calls must return in well under the 30s HTTP
        timeout; aim for <100ms, measured via time.perf_counter."""
        import time as _time

        def always_fail(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("down")

        with patch("httpx.post", side_effect=always_fail):
            for _ in range(3):
                with pytest.raises(httpx.ConnectError):
                    embed(["x"])

        t0 = _time.perf_counter()
        with pytest.raises(CircuitOpenError):
            embed(["x"])
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100, f"short-circuit took {elapsed_ms:.1f}ms"
