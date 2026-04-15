"""Tests for commonplace_worker/bluesky_auth.py.

All tests mock subprocess and atproto — no network calls.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from commonplace_worker.bluesky_auth import (
    _KEYCHAIN_ACCOUNT,
    _KEYCHAIN_SERVICE,
    BlueskyAuthError,
    clear_session_cache,
    create_session,
    refresh_session,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Ensure the in-process session cache is clean before each test."""
    clear_session_cache()
    yield  # type: ignore[misc]
    clear_session_cache()


def _make_fake_result(returncode: int = 0, stdout: str = "fakepassword") -> Any:
    """Return a fake subprocess.CompletedProcess-like object."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


def _make_fake_client(handle: str = "ithinkicam.bsky.social") -> MagicMock:
    """Return a mock atproto Client whose session looks authenticated."""
    client = MagicMock()
    session = MagicMock()
    session.handle = handle
    session.did = "did:plc:fakefakefake"
    session.access_jwt = "access.jwt.token"
    session.refresh_jwt = "refresh.jwt.token"
    client._session = session
    return client


# ---------------------------------------------------------------------------
# Test: keychain is queried with the right service/account
# ---------------------------------------------------------------------------


def test_keychain_queried_with_correct_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_session calls `security` with the configured account and service."""
    called_with: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> Any:
        called_with.append(list(cmd))
        return _make_fake_result(stdout="fakepassword")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    fake_client = _make_fake_client()

    with patch("commonplace_worker.bluesky_auth._build_client", return_value=fake_client):
        create_session()

    assert len(called_with) == 1
    cmd = called_with[0]
    assert "security" in cmd[0]
    assert "-a" in cmd
    assert _KEYCHAIN_ACCOUNT in cmd
    assert "-s" in cmd
    assert _KEYCHAIN_SERVICE in cmd
    assert "-w" in cmd


# ---------------------------------------------------------------------------
# Test: password is never in any log-accessible string
# ---------------------------------------------------------------------------


def test_password_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The app password must not appear in any log record."""
    secret_password = "super-secret-app-password-xyz"

    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _make_fake_result(stdout=secret_password)
    )
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    fake_client = _make_fake_client()

    with (
        caplog.at_level(logging.DEBUG, logger="commonplace_worker.bluesky_auth"),
        patch("commonplace_worker.bluesky_auth._build_client", return_value=fake_client),
    ):
        create_session()

    for record in caplog.records:
        assert secret_password not in record.getMessage(), (
            f"Password appeared in log record: {record.getMessage()!r}"
        )


# ---------------------------------------------------------------------------
# Test: returned session dict contains expected keys
# ---------------------------------------------------------------------------


def test_session_dict_has_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_session returns a dict with handle, did, accessJwt, refreshJwt."""
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _make_fake_result(stdout="fakepassword")
    )
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    fake_client = _make_fake_client()

    with patch("commonplace_worker.bluesky_auth._build_client", return_value=fake_client):
        session = create_session()

    assert "handle" in session
    assert "did" in session
    assert "accessJwt" in session
    assert "refreshJwt" in session
    assert session["handle"] == "ithinkicam.bsky.social"
    assert session["did"] == "did:plc:fakefakefake"
    assert session["accessJwt"] == "access.jwt.token"
    assert session["refreshJwt"] == "refresh.jwt.token"


# ---------------------------------------------------------------------------
# Test: auth failure surfaces BlueskyAuthError
# ---------------------------------------------------------------------------


def test_auth_401_raises_bluesky_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 from Bluesky login raises BlueskyAuthError with a clear message."""
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _make_fake_result(stdout="wrongpassword")
    )
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    # Simulate a 401 response from atproto
    fake_exc = Exception("Unauthorized")
    mock_response = MagicMock()
    mock_response.status_code = 401
    fake_exc.response = mock_response  # type: ignore[attr-defined]

    failing_client = MagicMock()
    failing_client.login.side_effect = fake_exc

    with (
        patch("commonplace_worker.bluesky_auth._build_client", return_value=failing_client),
        pytest.raises(BlueskyAuthError, match="rejected credentials"),
    ):
        create_session()


def test_auth_generic_failure_raises_bluesky_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic login exception raises BlueskyAuthError."""
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _make_fake_result(stdout="somepassword")
    )
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    failing_client = MagicMock()
    failing_client.login.side_effect = RuntimeError("connection refused")

    with (
        patch("commonplace_worker.bluesky_auth._build_client", return_value=failing_client),
        pytest.raises(BlueskyAuthError, match="login failed"),
    ):
        create_session()


def test_keychain_failure_raises_bluesky_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero keychain exit raises BlueskyAuthError."""
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _make_fake_result(returncode=44, stdout="")
    )
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    with pytest.raises(BlueskyAuthError, match="Keychain lookup failed"):
        create_session()


# ---------------------------------------------------------------------------
# Test: session is cached in-process (login called only once)
# ---------------------------------------------------------------------------


def test_session_cached_on_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call to create_session returns cached result without re-logging in."""
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _make_fake_result(stdout="fakepassword")
    )
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    fake_client = _make_fake_client()

    with patch("commonplace_worker.bluesky_auth._build_client", return_value=fake_client):
        s1 = create_session()
        s2 = create_session()

    assert s1 is s2
    # login was only called once despite two create_session() calls
    fake_client.login.assert_called_once()


# ---------------------------------------------------------------------------
# Test: refresh_session works with injected client
# ---------------------------------------------------------------------------


def test_refresh_session_returns_updated_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """refresh_session returns a session dict from the refreshed client."""
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "ithinkicam.bsky.social")

    fake_client = _make_fake_client()
    fake_client._session.access_jwt = "new.access.jwt"

    session = refresh_session("old.refresh.jwt", _client=fake_client)
    assert session["accessJwt"] == "new.access.jwt"


# ---------------------------------------------------------------------------
# Test: handle from env wins over pins file
# ---------------------------------------------------------------------------


def test_env_handle_wins_over_pins_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """COMMONPLACE_BLUESKY_HANDLE env var takes priority over pins file."""
    monkeypatch.setenv("COMMONPLACE_BLUESKY_HANDLE", "override.bsky.social")
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _make_fake_result(stdout="fakepassword")
    )

    fake_client = _make_fake_client(handle="override.bsky.social")

    with patch("commonplace_worker.bluesky_auth._build_client", return_value=fake_client):
        session = create_session()

    assert session["handle"] == "override.bsky.social"
