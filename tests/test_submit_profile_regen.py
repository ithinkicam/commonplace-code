"""Smoke tests for scripts/submit_profile_regen.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Ensure scripts/ is importable
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _import_submit() -> object:
    """Import submit_profile_regen.main without executing the script."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "submit_profile_regen",
        str(_SCRIPTS_DIR / "submit_profile_regen.py"),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """--dry-run should exit 0 and print a dry-run JSON line."""
    mod = _import_submit()
    rc = mod.main(["--dry-run"])  # type: ignore[attr-defined]
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_submit_enqueues_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --dry-run, main() should connect to DB, submit job, and exit 0."""
    import sqlite_vec  # type: ignore[import-untyped]

    import commonplace_db.db as _db_module
    from commonplace_db.db import migrate

    db_path = tmp_path / "test.db"
    prep_conn = sqlite3.connect(str(db_path))
    prep_conn.row_factory = sqlite3.Row
    prep_conn.enable_load_extension(True)
    sqlite_vec.load(prep_conn)
    prep_conn.enable_load_extension(False)
    migrate(prep_conn)
    prep_conn.close()

    # Patch the module-level DB_PATH (set at import time from env var)
    monkeypatch.setattr(_db_module, "DB_PATH", str(db_path))

    mod = _import_submit()
    rc = mod.main([])  # type: ignore[attr-defined]
    assert rc == 0

    # Verify job was actually enqueued
    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    check_conn.enable_load_extension(True)
    sqlite_vec.load(check_conn)
    check_conn.enable_load_extension(False)

    row = check_conn.execute(
        "SELECT kind, status FROM job_queue WHERE kind = 'regenerate_profile'"
    ).fetchone()
    assert row is not None
    assert row["kind"] == "regenerate_profile"
    assert row["status"] == "queued"
    check_conn.close()
