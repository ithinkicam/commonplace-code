"""Handler sub-package for the Commonplace worker.

Each module in this package defines one or more handler functions.
Import the HANDLERS dict from this package to get all registered handlers.
"""

from __future__ import annotations

from commonplace_worker.handlers.bluesky import handle_bluesky_ingest
from commonplace_worker.handlers.kindle import handle_kindle_ingest
from commonplace_worker.handlers.library import handle_library_ingest

LIBRARY_HANDLERS: dict[str, object] = {
    "ingest_library": handle_library_ingest,
}

BLUESKY_HANDLERS: dict[str, object] = {
    "ingest_bluesky": handle_bluesky_ingest,
}

KINDLE_HANDLERS: dict[str, object] = {
    "ingest_kindle": handle_kindle_ingest,
}

__all__ = [
    "handle_library_ingest",
    "handle_bluesky_ingest",
    "handle_kindle_ingest",
    "LIBRARY_HANDLERS",
    "BLUESKY_HANDLERS",
    "KINDLE_HANDLERS",
]
