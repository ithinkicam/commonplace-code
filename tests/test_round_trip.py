"""Round-trip test closing Phase 1 (task 1_10).

Exercises the full pipeline end to end:

    POST /capture  →  inbox file written + job enqueued
                  →  worker claims job via poll_once
                  →  capture handler moves file inbox → vault/captured
                  →  job_queue row marked 'complete'
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import commonplace_db
from commonplace_worker.worker import HANDLERS, poll_once

BEARER = "test-round-trip-bearer"
VALID_BODY = {
    "source": "round-trip-test",
    "kind": "url",
    "content": "https://example.com/round-trip",
}


@pytest.fixture
def wired_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path, Path, str]:
    """Point server + worker at the same tmp DB, inbox, and vault."""
    db_file = str(tmp_path / "round_trip.db")
    inbox_dir = tmp_path / "inbox"
    vault_dir = tmp_path / "captured"
    inbox_dir.mkdir()

    monkeypatch.setenv("COMMONPLACE_CAPTURE_BEARER", BEARER)
    monkeypatch.setenv("COMMONPLACE_DB_PATH", db_file)
    monkeypatch.setenv("COMMONPLACE_INBOX_DIR", str(inbox_dir))
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(vault_dir))

    import commonplace_server.server as server_mod

    monkeypatch.setattr(server_mod, "_CAPTURE_BEARER", BEARER)

    client = TestClient(server_mod.mcp.http_app(), raise_server_exceptions=True)
    return client, inbox_dir, vault_dir, db_file


def test_round_trip_capture_to_vault(
    wired_env: tuple[TestClient, Path, Path, str],
) -> None:
    """POST /capture → poll_once → file in vault, job complete."""
    client, inbox_dir, vault_dir, db_file = wired_env

    response = client.post(
        "/capture",
        json=VALID_BODY,
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 202, response.text
    data = response.json()
    job_id = data["job_id"]
    filename = data["inbox_file"]

    assert (inbox_dir / filename).exists()
    assert not vault_dir.exists() or not (vault_dir / filename).exists()

    conn = commonplace_db.connect(db_file)
    try:
        commonplace_db.migrate(conn)

        processed = poll_once(conn, HANDLERS)
        assert processed == 1

        row = conn.execute(
            "SELECT status, error, completed_at FROM job_queue WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["status"] == "complete", f"job row: {dict(row)}"
    assert row["error"] is None
    assert row["completed_at"] is not None

    assert not (inbox_dir / filename).exists(), "file should have left inbox"
    assert (vault_dir / filename).exists(), "file should be in vault/captured"


def test_round_trip_missing_inbox_file_marks_failed(
    wired_env: tuple[TestClient, Path, Path, str],
) -> None:
    """If the inbox file disappears between capture and poll, the job is failed cleanly."""
    client, inbox_dir, vault_dir, db_file = wired_env

    response = client.post(
        "/capture",
        json=VALID_BODY,
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    filename = response.json()["inbox_file"]

    (inbox_dir / filename).unlink()

    conn = commonplace_db.connect(db_file)
    try:
        commonplace_db.migrate(conn)
        processed = poll_once(conn, HANDLERS)
        assert processed == 1
        row = conn.execute(
            "SELECT status, error FROM job_queue WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row["status"] == "failed"
    assert "inbox file not found" in row["error"]
