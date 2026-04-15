"""Tests for the POST /capture endpoint (task 1_6_capture_endpoint).

Covers:
- handle_capture() pure-function tests (auth, validation, file writes, job enqueue)
- Route-level tests via Starlette TestClient against mcp.http_app()
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import commonplace_db
from commonplace_server.capture import handle_capture, resolve_bearer

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_BODY: dict[str, Any] = {
    "source": "android-shortcut",
    "kind": "url",
    "content": "https://example.com",
}

BEARER = "test-bearer-token"


@pytest.fixture
def migrated_conn(tmp_path: Path) -> sqlite3.Connection:
    """In-memory SQLite connection with migrations applied."""
    db_file = str(tmp_path / "capture_test.db")
    conn = commonplace_db.connect(db_file)
    commonplace_db.migrate(conn)
    return conn


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    """Temporary inbox directory."""
    d = tmp_path / "inbox"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# resolve_bearer tests
# ---------------------------------------------------------------------------


def test_resolve_bearer_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_bearer returns the env var value when set."""
    monkeypatch.setenv("COMMONPLACE_CAPTURE_BEARER", "my-token")
    assert resolve_bearer() == "my-token"


def test_resolve_bearer_env_var_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var takes precedence over keychain."""
    monkeypatch.setenv("COMMONPLACE_CAPTURE_BEARER", "env-token")
    # Even if keychain were available, env should win
    assert resolve_bearer() == "env-token"


def test_resolve_bearer_returns_none_when_neither_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_bearer returns None when env var missing and keychain unavailable."""
    monkeypatch.delenv("COMMONPLACE_CAPTURE_BEARER", raising=False)
    # Patch subprocess to simulate missing keychain entry
    import subprocess

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        class _Result:
            returncode = 44
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = resolve_bearer()
    assert result is None


# ---------------------------------------------------------------------------
# handle_capture — auth checks
# ---------------------------------------------------------------------------


def test_valid_request_returns_202(migrated_conn: sqlite3.Connection, inbox: Path) -> None:
    """A valid request with correct bearer returns 202 and creates inbox file."""
    status, body = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 202
    assert body["status"] == "accepted"
    assert "job_id" in body
    assert "inbox_file" in body


def test_valid_request_creates_inbox_file(migrated_conn: sqlite3.Connection, inbox: Path) -> None:
    """Successful capture writes a JSON file to the inbox directory."""
    status, body = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 202
    inbox_file = inbox / body["inbox_file"]
    assert inbox_file.exists(), f"Expected {inbox_file} to exist"
    with inbox_file.open() as fh:
        data = json.load(fh)
    assert data["source"] == VALID_BODY["source"]
    assert data["kind"] == VALID_BODY["kind"]
    assert data["content"] == VALID_BODY["content"]


def test_valid_request_enqueues_job(migrated_conn: sqlite3.Connection, inbox: Path) -> None:
    """Successful capture enqueues a job with kind='capture' referencing the inbox file."""
    status, body = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 202
    job_id = body["job_id"]
    inbox_file = body["inbox_file"]

    row = migrated_conn.execute(
        "SELECT kind, payload FROM job_queue WHERE id = ?", (job_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "capture"
    payload = json.loads(row[1])
    assert payload["inbox_file"] == inbox_file


def test_missing_authorization_header_returns_401(
    migrated_conn: sqlite3.Connection, inbox: Path
) -> None:
    """Missing Authorization header → 401."""
    status, body = handle_capture(
        VALID_BODY.copy(),
        None,
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 401
    assert "error" in body


def test_wrong_bearer_returns_401(migrated_conn: sqlite3.Connection, inbox: Path) -> None:
    """Wrong bearer token → 401; response must not echo the expected token."""
    status, body = handle_capture(
        VALID_BODY.copy(),
        "Bearer wrong-token",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 401
    assert BEARER not in json.dumps(body), "Response must not echo the expected bearer"


def test_no_bearer_configured_returns_503(migrated_conn: sqlite3.Connection, inbox: Path) -> None:
    """expected_bearer=None → 503 on every request."""
    status, body = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=None,
    )
    assert status == 503
    assert "error" in body


# ---------------------------------------------------------------------------
# handle_capture — body validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", ["source", "kind", "content"])
def test_missing_required_field_returns_400(
    missing_field: str,
    migrated_conn: sqlite3.Connection,
    inbox: Path,
) -> None:
    """Missing source/kind/content → 400 with a clear error message."""
    body = {k: v for k, v in VALID_BODY.items() if k != missing_field}
    status, resp = handle_capture(
        body,
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 400
    assert "error" in resp
    assert missing_field in resp["error"]


def test_metadata_is_included_in_inbox_file(
    migrated_conn: sqlite3.Connection, inbox: Path
) -> None:
    """Optional metadata field is written to the inbox file when provided."""
    body = {**VALID_BODY, "metadata": {"tags": ["test"], "priority": 1}}
    status, resp = handle_capture(
        body,
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 202
    inbox_file = inbox / resp["inbox_file"]
    data = json.load(inbox_file.open())
    assert data["metadata"] == {"tags": ["test"], "priority": 1}


# ---------------------------------------------------------------------------
# Inbox file naming
# ---------------------------------------------------------------------------


def test_filename_format(migrated_conn: sqlite3.Connection, inbox: Path) -> None:
    """Inbox filename follows YYYY-MM-DDTHHMMSSZ_<8-char-hash>.json pattern."""
    import re

    status, body = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status == 202
    name = body["inbox_file"]
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{6}Z_[0-9a-f]{8}\.json$"
    assert re.match(pattern, name), f"Filename '{name}' does not match expected pattern"


def test_hash_is_deterministic_for_identical_bodies(
    migrated_conn: sqlite3.Connection, inbox: Path
) -> None:
    """The 8-char hash portion is the same for two requests with identical bodies."""
    import re

    status1, body1 = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    # Wait a moment so timestamps differ but use same body
    import time

    time.sleep(1.1)

    status2, body2 = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status1 == 202
    assert status2 == 202

    hash1 = re.search(r"_([0-9a-f]{8})\.json$", body1["inbox_file"])
    hash2 = re.search(r"_([0-9a-f]{8})\.json$", body2["inbox_file"])
    assert hash1 and hash2
    assert hash1.group(1) == hash2.group(1), "Hash should be the same for identical bodies"


def test_filenames_are_monotonically_sortable(
    migrated_conn: sqlite3.Connection, inbox: Path
) -> None:
    """Two successive captures produce filenames that sort chronologically."""
    import time

    status1, body1 = handle_capture(
        VALID_BODY.copy(),
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    time.sleep(1.1)
    body2_data = {**VALID_BODY, "content": "https://different.com"}
    status2, body2 = handle_capture(
        body2_data,
        f"Bearer {BEARER}",
        conn=migrated_conn,
        inbox_dir=inbox,
        expected_bearer=BEARER,
    )
    assert status1 == 202
    assert status2 == 202
    names = sorted([body1["inbox_file"], body2["inbox_file"]])
    assert names[0] == body1["inbox_file"], "First file should sort before second"


# ---------------------------------------------------------------------------
# Route-level tests via Starlette TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def capture_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient configured for the /capture route with test bearer and inbox."""
    db_file = str(tmp_path / "route_test.db")
    inbox_dir = str(tmp_path / "inbox")

    monkeypatch.setenv("COMMONPLACE_CAPTURE_BEARER", BEARER)
    monkeypatch.setenv("COMMONPLACE_DB_PATH", db_file)
    monkeypatch.setenv("COMMONPLACE_INBOX_DIR", inbox_dir)

    # Force re-resolution of the bearer in the server module
    import commonplace_server.server as server_mod

    monkeypatch.setattr(server_mod, "_CAPTURE_BEARER", BEARER)

    app = server_mod.mcp.http_app()
    return TestClient(app, raise_server_exceptions=True)


def test_route_valid_request_returns_202(capture_client: TestClient, tmp_path: Path) -> None:
    """Valid POST /capture via TestClient returns 202."""
    response = capture_client.post(
        "/capture",
        json=VALID_BODY,
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert "job_id" in data
    assert "inbox_file" in data


def test_route_missing_auth_returns_401(capture_client: TestClient) -> None:
    """POST /capture without Authorization header returns 401."""
    response = capture_client.post("/capture", json=VALID_BODY)
    assert response.status_code == 401


def test_route_wrong_bearer_returns_401(capture_client: TestClient) -> None:
    """POST /capture with wrong bearer returns 401."""
    response = capture_client.post(
        "/capture",
        json=VALID_BODY,
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401
    assert BEARER not in response.text


def test_route_missing_field_returns_400(capture_client: TestClient) -> None:
    """POST /capture with missing 'content' field returns 400."""
    body = {"source": "test", "kind": "text"}
    response = capture_client.post(
        "/capture",
        json=body,
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 400


def test_route_non_json_body_returns_400(capture_client: TestClient) -> None:
    """POST /capture with non-JSON body returns 400."""
    response = capture_client.post(
        "/capture",
        content=b"not-json",
        headers={
            "Authorization": f"Bearer {BEARER}",
            "Content-Type": "text/plain",
        },
    )
    assert response.status_code == 400


def test_route_no_bearer_configured_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _CAPTURE_BEARER is None, all POST /capture requests return 503."""
    db_file = str(tmp_path / "no_bearer.db")
    inbox_dir = str(tmp_path / "inbox")

    monkeypatch.setenv("COMMONPLACE_DB_PATH", db_file)
    monkeypatch.setenv("COMMONPLACE_INBOX_DIR", inbox_dir)
    monkeypatch.delenv("COMMONPLACE_CAPTURE_BEARER", raising=False)

    import commonplace_server.server as server_mod

    monkeypatch.setattr(server_mod, "_CAPTURE_BEARER", None)

    app = server_mod.mcp.http_app()
    client = TestClient(app, raise_server_exceptions=True)
    response = client.post(
        "/capture",
        json=VALID_BODY,
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 503


def test_route_inbox_file_created(capture_client: TestClient, tmp_path: Path) -> None:
    """A successful POST /capture creates the inbox file on disk."""
    inbox_dir = tmp_path / "inbox"
    response = capture_client.post(
        "/capture",
        json=VALID_BODY,
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 202
    filename = response.json()["inbox_file"]
    inbox_file = inbox_dir / filename
    assert inbox_file.exists(), f"Expected inbox file {inbox_file} to exist"
