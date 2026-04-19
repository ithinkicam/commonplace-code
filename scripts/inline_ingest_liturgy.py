"""Inline runner for liturgy_bcp and liturgy_lff handlers.

Bypasses the job queue to invoke the handlers directly against the live DB.
Used for Commonplace task 4.8 pass 2c (worker was locked on another job;
liturgical ingestion run inline while worker stopped).
"""

from __future__ import annotations

import sys
import time
import traceback

from commonplace_db import connect


def run_bcp() -> None:
    from commonplace_worker.handlers.liturgy_bcp import handle_liturgy_bcp_ingest

    conn = connect()
    try:
        t0 = time.monotonic()
        print("=== BCP ingest start ===", flush=True)
        result = handle_liturgy_bcp_ingest({}, conn)
        conn.commit()
        elapsed = time.monotonic() - t0
        print(f"=== BCP ingest end (elapsed={elapsed:.1f}s) ===", flush=True)
        print(f"Result: {result}", flush=True)
    finally:
        conn.close()


def run_lff() -> None:
    from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

    conn = connect()
    try:
        t0 = time.monotonic()
        print("=== LFF ingest start ===", flush=True)
        result = handle_liturgy_lff_ingest({}, conn)
        conn.commit()
        elapsed = time.monotonic() - t0
        print(f"=== LFF ingest end (elapsed={elapsed:.1f}s) ===", flush=True)
        print(f"Result: {result}", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    try:
        if which in ("bcp", "both"):
            run_bcp()
        if which in ("lff", "both"):
            run_lff()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
