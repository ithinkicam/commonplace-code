"""Unit tests for scripts/book_enrichment_scan.py."""

from __future__ import annotations

import io
import itertools
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from commonplace_db.db import connect, migrate

_DOC_COUNTER = itertools.count(1)


# ---------------------------------------------------------------------------
# Helper: load the scan script as a module
# ---------------------------------------------------------------------------


def _load_scan_module():
    """Dynamically load book_enrichment_scan to avoid sys.path issues."""
    import importlib.util

    scripts_dir = Path(__file__).parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "book_enrichment_scan",
        scripts_dir / "book_enrichment_scan.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _run_scan(argv: list[str], conn: sqlite3.Connection | None = None) -> tuple[int, str]:
    """Run the scan main() with mocked DB and capture stdout."""
    mod = _load_scan_module()

    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn or _make_db()),
        patch("commonplace_db.db.migrate"),
    ):
        rc = mod.main(argv)
    return rc, buf.getvalue()


def _make_db() -> sqlite3.Connection:
    """Create an in-memory DB with migrations applied."""
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _insert_doc(
    conn: sqlite3.Connection,
    content_type: str,
    title: str = "A Book",
    enriched_at: str | None = None,
    description: str | None = None,
) -> int:
    uid = next(_DOC_COUNTER)
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (content_type, title, content_hash, status, enriched_at, description) "
            "VALUES (?, ?, ?, 'complete', ?, ?)",
            (content_type, title, f"hash_{uid}_{content_type}", enriched_at, description),
        )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Dry-run: reports by content_type without enqueuing
# ---------------------------------------------------------------------------


def test_dry_run_counts_eligible_by_type() -> None:
    """--dry-run reports eligible counts per content_type."""
    conn = _make_db()
    _insert_doc(conn, "storygraph_entry")
    _insert_doc(conn, "storygraph_entry")
    _insert_doc(conn, "audiobook")
    _insert_doc(conn, "book")

    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn),
        patch("commonplace_db.db.migrate"),
    ):
        rc = mod.main(["--dry-run"])

    output = buf.getvalue()
    assert rc == 0
    assert "storygraph_entry: 2" in output
    assert "audiobook: 1" in output
    assert "book: 1" in output
    assert "Total eligible: 4" in output


def test_dry_run_excludes_already_enriched() -> None:
    """--dry-run does not count already-enriched documents (without --force)."""
    conn = _make_db()
    _insert_doc(conn, "storygraph_entry")  # unenriched
    _insert_doc(
        conn,
        "storygraph_entry",
        title="Already Enriched",
        enriched_at="2024-01-01T00:00:00Z",
        description="Has description.",
    )  # enriched

    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn),
        patch("commonplace_db.db.migrate"),
    ):
        mod.main(["--dry-run"])

    output = buf.getvalue()
    assert "Total eligible: 1" in output


def test_dry_run_excludes_ineligible_content_types() -> None:
    """--dry-run does not count non-book content_types."""
    conn = _make_db()
    _insert_doc(conn, "bluesky_post")
    _insert_doc(conn, "capture")
    _insert_doc(conn, "storygraph_entry")  # eligible

    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn),
        patch("commonplace_db.db.migrate"),
    ):
        mod.main(["--dry-run"])

    output = buf.getvalue()
    assert "Total eligible: 1" in output


def test_dry_run_does_not_enqueue() -> None:
    """--dry-run never calls submit."""
    conn = _make_db()
    _insert_doc(conn, "audiobook")

    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn),
        patch("commonplace_db.db.migrate"),
        patch("commonplace_server.jobs.submit") as mock_submit,
    ):
        rc = mod.main(["--dry-run"])

    mock_submit.assert_not_called()
    assert rc == 0


# ---------------------------------------------------------------------------
# --content-type filtering
# ---------------------------------------------------------------------------


def test_content_type_filter() -> None:
    """--content-type scopes results to the specified type."""
    conn = _make_db()
    _insert_doc(conn, "storygraph_entry")
    _insert_doc(conn, "audiobook")
    _insert_doc(conn, "book")

    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn),
        patch("commonplace_db.db.migrate"),
    ):
        mod.main(["--dry-run", "--content-type", "audiobook"])

    output = buf.getvalue()
    assert "Total eligible: 1" in output


def test_invalid_content_type_exits_with_error() -> None:
    """--content-type with invalid value returns exit code 1."""
    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=_make_db()),
        patch("commonplace_db.db.migrate"),
    ):
        rc = mod.main(["--dry-run", "--content-type", "not_a_valid_type"])

    assert rc == 1


# ---------------------------------------------------------------------------
# --force flag
# ---------------------------------------------------------------------------


def test_force_includes_already_enriched() -> None:
    """--force includes already-enriched documents in dry-run count."""
    conn = _make_db()
    _insert_doc(conn, "storygraph_entry")
    _insert_doc(
        conn,
        "storygraph_entry",
        title="Enriched",
        enriched_at="2024-01-01T00:00:00Z",
        description="Description.",
    )

    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn),
        patch("commonplace_db.db.migrate"),
    ):
        mod.main(["--dry-run", "--force"])

    output = buf.getvalue()
    assert "Total eligible: 2" in output


# ---------------------------------------------------------------------------
# Limit flag
# ---------------------------------------------------------------------------


def test_dry_run_with_limit() -> None:
    """--limit N caps the would_enqueue count."""
    conn = _make_db()
    for i in range(5):
        _insert_doc(conn, "storygraph_entry", title=f"Book {i}")

    mod = _load_scan_module()
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch("commonplace_db.db.connect", return_value=conn),
        patch("commonplace_db.db.migrate"),
    ):
        mod.main(["--dry-run", "--limit", "3"])

    output = buf.getvalue()
    assert "Would enqueue: 3" in output
