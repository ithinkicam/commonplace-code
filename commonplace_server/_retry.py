"""Shared retry + exponential-backoff helper for external HTTP calls.

Callers (``tmdb``, ``openlibrary``, ``google_books`` and any future
enrichment / lookup client) use ``retry_http_get`` so transient upstream
failures no longer abort a batch enrichment run on the first blip.

Design
------
- Retry only on *transient* conditions: connect/transport errors,
  server 5xx, and 429 (Too Many Requests). 4xx other than 429 are
  permanent (bad key, not found, banned), so retrying wastes quota
  and masks real problems — those propagate on the first attempt.
- Exponential backoff with small jitter: 0.5s, 1.0s, 2.0s + up to
  ±25% jitter. Capped attempts default to 3 so the worst case is a
  ~3.5s delay before giving up.
- Returns the ``httpx.Response`` so the caller controls
  ``raise_for_status`` / JSON parsing semantics — the helper does not
  force a particular error-handling style on its callers.
- Stateless; no circuit breaker. Upstreams that stay down for minutes
  should use a circuit breaker at the caller (see ``embedding.py`` for
  the pattern); this helper is the fast-retry inner layer.

Callers typically wrap usage in their own ``try/except: return None``
to preserve the graceful-None behaviour already expected from
enrichment helpers.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.TransportError,
)


def _sleep_with_jitter(base_delay: float, attempt: int) -> None:
    """Sleep ``base_delay * 2**attempt`` plus up to ±25% jitter."""
    nominal = base_delay * (2**attempt)
    jitter = nominal * 0.25 * (2 * random.random() - 1)
    time.sleep(max(nominal + jitter, 0.01))


def retry_http_get(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> httpx.Response:
    """GET ``url`` with exponential backoff on transient failures.

    Retries on: connect/transport errors, HTTP 5xx, and HTTP 429. Other
    4xx responses return immediately (caller inspects ``response``).

    Raises the last exception after ``max_attempts`` unsuccessful tries.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = httpx.get(
                url, params=params, headers=headers, timeout=timeout
            )
        except _RETRY_EXCEPTIONS as exc:
            last_exc = exc
            if attempt + 1 < max_attempts:
                logger.debug(
                    "HTTP %s attempt %d/%d failed with %s; retrying",
                    url, attempt + 1, max_attempts, exc.__class__.__name__,
                )
                _sleep_with_jitter(base_delay, attempt)
                continue
            raise

        if response.status_code in _RETRY_STATUS_CODES and attempt + 1 < max_attempts:
            logger.debug(
                "HTTP %s attempt %d/%d returned %d; retrying",
                url, attempt + 1, max_attempts, response.status_code,
            )
            _sleep_with_jitter(base_delay, attempt)
            continue

        return response

    # Unreachable — either we returned a Response or raised above. Keeping
    # an explicit raise as a safety net in case the loop logic evolves.
    assert last_exc is not None
    raise last_exc
