"""Phase 0.0 smoke test. Proves the test infrastructure works end-to-end."""

from __future__ import annotations

import commonplace_server
import commonplace_worker


def test_server_version() -> None:
    assert commonplace_server.__version__ == "0.0.1"


def test_worker_version() -> None:
    assert commonplace_worker.__version__ == "0.0.1"
