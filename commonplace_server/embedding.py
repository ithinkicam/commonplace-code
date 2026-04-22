"""Ollama embedding client.

Wraps the Ollama /api/embeddings endpoint (v0.20.7 API).
Asserts vector dimension == 768 on every response.
Provides pack/unpack helpers for little-endian float32 BLOB storage.

Includes a small circuit breaker around the HTTP call: after
``_FAILURE_THRESHOLD`` consecutive failures the breaker OPENS and further
``embed()`` calls short-circuit with ``CircuitOpenError`` until the
``_COOL_DOWN_SECONDS`` grace period elapses, at which point a single trial
call probes whether Ollama is back. Without the breaker, a worker that
slams embed() during an Ollama outage pays the full 30s×(retries+1) per
call before surfacing the failure, turning a short outage into a long
queue-drain delay.
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_OLLAMA_BASE = "http://localhost:11434"
_EMBED_URL = f"{_OLLAMA_BASE}/api/embeddings"
_EXPECTED_DIM = 768
_TIMEOUT = 30.0   # seconds per request
_MAX_RETRIES = 1  # one retry on transient HTTP failure

# Circuit breaker tuning. Tripped after three consecutive embed-call
# failures (enough to distinguish a true outage from a single transient
# blip that _MAX_RETRIES already handles), cools down for 60s before a
# single trial call probes recovery.
_FAILURE_THRESHOLD = 3
_COOL_DOWN_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EmbeddingDimensionError(ValueError):
    """Raised when Ollama returns a vector of unexpected length."""


class CircuitOpenError(RuntimeError):
    """Raised when embed() is short-circuited because Ollama is unavailable.

    Callers should treat this as a fast-fail signal — retry later, don't
    sleep through a 30s HTTP timeout. The breaker re-tries automatically
    after ``_COOL_DOWN_SECONDS``.
    """


# ---------------------------------------------------------------------------
# Circuit breaker state
# ---------------------------------------------------------------------------


class _CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _Circuit:
    state: _CircuitState = _CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None


# Module-level state protected by a lock. Worker polls one job at a time
# today so contention is nil, but the lock future-proofs against async or
# threaded callers; it never blocks on the HTTP call itself.
_circuit = _Circuit()
_circuit_lock = threading.Lock()


def _before_call() -> None:
    """Guard entering the HTTP call; raises CircuitOpenError when open."""
    with _circuit_lock:
        if _circuit.state is not _CircuitState.OPEN:
            return
        assert _circuit.opened_at is not None
        remaining = _COOL_DOWN_SECONDS - (time.monotonic() - _circuit.opened_at)
        if remaining <= 0:
            _circuit.state = _CircuitState.HALF_OPEN
            logger.info("Ollama circuit: HALF_OPEN — probing recovery")
            return
        raise CircuitOpenError(
            f"Ollama circuit open (cooling down {remaining:.1f}s more "
            f"after {_FAILURE_THRESHOLD} consecutive failures)"
        )


def _after_success() -> None:
    with _circuit_lock:
        previous = _circuit.state
        _circuit.state = _CircuitState.CLOSED
        _circuit.consecutive_failures = 0
        _circuit.opened_at = None
        if previous is _CircuitState.HALF_OPEN:
            logger.info("Ollama circuit: CLOSED — recovery succeeded")


def _after_failure() -> None:
    with _circuit_lock:
        _circuit.consecutive_failures += 1
        if _circuit.consecutive_failures >= _FAILURE_THRESHOLD:
            if _circuit.state is not _CircuitState.OPEN:
                logger.warning(
                    "Ollama circuit: OPEN after %d consecutive failures; "
                    "subsequent calls will short-circuit for %.0fs",
                    _circuit.consecutive_failures,
                    _COOL_DOWN_SECONDS,
                )
            _circuit.state = _CircuitState.OPEN
            _circuit.opened_at = time.monotonic()


def _reset_circuit_for_tests() -> None:
    """Reset circuit state. Exposed for test isolation."""
    with _circuit_lock:
        _circuit.state = _CircuitState.CLOSED
        _circuit.consecutive_failures = 0
        _circuit.opened_at = None


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
    """POST to Ollama and return the embedding vector.  Retries once.

    Short-circuits with ``CircuitOpenError`` when the breaker is OPEN so
    the caller fails fast instead of paying ``_TIMEOUT × (retries+1)``
    per call during an Ollama outage.
    """
    _before_call()

    payload = {"model": model, "prompt": text}
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = httpx.post(_EMBED_URL, json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            _after_success()
            return list(data["embedding"])
        except (httpx.HTTPError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                continue
            _after_failure()
            raise

    # Should never reach here, but satisfies type-checker
    assert last_exc is not None
    raise last_exc
