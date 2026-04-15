"""Bluesky authentication helpers for the Commonplace worker.

Provides create_session() and refresh_session() backed by the atproto Python
package (pinned to 0.0.65 — last tested stable release).

Credentials
-----------
- Handle: read from env var COMMONPLACE_BLUESKY_HANDLE (wins) or from
  build/pins/bluesky.md ("Handle:" line).
- App password: read ONLY from macOS keychain via `security find-generic-password`.
  Never written to any file, log, or string variable that leaks.

Session caching
---------------
Sessions are cached in-process in ``_SESSION_CACHE``.  The cache holds one
entry keyed on handle.  ``create_session()`` returns the cached session if one
exists; callers who want a fresh token should call ``refresh_session()`` with
the stored refreshJwt.

atproto version note
--------------------
Pinned to 0.0.65.  The Client.login() / export_session_string() API is stable
across the 0.0.x series as of this pin.  If a future version makes breaking
changes, update pyproject.toml and re-test.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HANDLE_ENV = "COMMONPLACE_BLUESKY_HANDLE"
_PINS_FILE = Path(__file__).parent.parent / "build" / "pins" / "bluesky.md"
_KEYCHAIN_ACCOUNT = "commonplace"
_KEYCHAIN_SERVICE = "commonplace-bluesky/app-password"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BlueskyAuthError(Exception):
    """Raised when Bluesky authentication fails or is explicitly rejected."""


# ---------------------------------------------------------------------------
# In-process caches  {handle: session_dict | client}
# ---------------------------------------------------------------------------

_SESSION_CACHE: dict[str, Any] = {}
_CLIENT_CACHE: dict[str, Any] = {}  # handle -> authenticated atproto Client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_handle() -> str:
    """Return the Bluesky handle from env or pins file."""
    env_val = os.environ.get(_HANDLE_ENV)
    if env_val:
        return env_val.strip()

    if not _PINS_FILE.exists():
        raise BlueskyAuthError(
            f"build/pins/bluesky.md not found and {_HANDLE_ENV} is not set; "
            "cannot determine Bluesky handle."
        )

    text = _PINS_FILE.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]?\s*\*{0,2}Handle:\*{0,2}\s*`?([^\s`]+)`?", line, re.IGNORECASE)
        if m:
            return m.group(1).strip()

    raise BlueskyAuthError(
        f"Could not find 'Handle:' line in {_PINS_FILE}. "
        "Check the pins file format or set COMMONPLACE_BLUESKY_HANDLE."
    )


def _read_password() -> str:
    """Read the app password from the macOS keychain.

    Uses `security find-generic-password` via subprocess.  The result is
    returned as a string and MUST NOT be logged or stored in any persistent
    location.
    """
    try:
        result = subprocess.run(  # noqa: S603
            [
                "security",
                "find-generic-password",
                "-a", _KEYCHAIN_ACCOUNT,
                "-s", _KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise BlueskyAuthError(
            "`security` binary not found — are you on macOS?"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BlueskyAuthError("Keychain query timed out.") from exc

    if result.returncode != 0:
        raise BlueskyAuthError(
            f"Keychain lookup failed (exit {result.returncode}). "
            "Run: security add-generic-password -U -a commonplace "
            "-s commonplace-bluesky/app-password -w '<app-password>'"
        )

    password = result.stdout.strip()
    if not password:
        raise BlueskyAuthError(
            "Keychain returned an empty password for "
            f"account={_KEYCHAIN_ACCOUNT!r} service={_KEYCHAIN_SERVICE!r}."
        )
    return password


def _build_client() -> Any:
    """Return a new atproto Client instance."""
    from atproto import Client  # type: ignore[import-untyped]

    return Client()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_session(*, _client: Any = None) -> dict[str, Any]:
    """Authenticate with Bluesky and return a session dict.

    Returns a dict with at least:
        handle       str
        did          str
        accessJwt    str
        refreshJwt   str

    If a session for this handle is already cached in-process, returns the
    cached session without re-authenticating.

    Parameters
    ----------
    _client:
        Seam for tests.  If provided, must be an already-configured atproto
        Client-like object (``login`` will not be called again).
    """
    handle = _read_handle()

    if handle in _SESSION_CACHE:
        cached: dict[str, Any] = _SESSION_CACHE[handle]
        return cached

    if _client is not None:
        client = _client
    else:
        password = _read_password()
        client = _build_client()
        try:
            client.login(handle, password)
        except Exception as exc:
            # Mask the password — never let it appear in logs or tracebacks.
            msg = str(exc)
            status = getattr(exc, "response", None)
            if status is not None:
                code = getattr(status, "status_code", None)
                if code in (401, 403):
                    raise BlueskyAuthError(
                        f"Bluesky rejected credentials for {handle!r} "
                        f"(HTTP {code}). Check or rotate the app password."
                    ) from None
            # Re-raise with a sanitised message — no password in exc chain.
            raise BlueskyAuthError(
                f"Bluesky login failed for {handle!r}: {msg}"
            ) from None

    session_data = _extract_session(client, handle)
    _SESSION_CACHE[handle] = session_data
    _CLIENT_CACHE[handle] = client
    return session_data


def refresh_session(refresh_jwt: str, *, _client: Any = None) -> dict[str, Any]:
    """Refresh an expired access token.

    Parameters
    ----------
    refresh_jwt:
        The refreshJwt from the previous session.
    _client:
        Seam for tests.

    Returns
    -------
    Updated session dict with new accessJwt.
    """
    handle = _read_handle()

    if _client is not None:
        client = _client
    else:
        from atproto import Client  # noqa: PLC0415

        client = Client()
        try:
            client.login(session_string=refresh_jwt)
        except Exception as exc:
            msg = str(exc)
            status = getattr(exc, "response", None)
            if status is not None:
                code = getattr(status, "status_code", None)
                if code in (401, 403):
                    raise BlueskyAuthError(
                        f"Token refresh rejected for {handle!r} (HTTP {code}). "
                        "Re-authenticate with create_session()."
                    ) from None
            raise BlueskyAuthError(f"Token refresh failed: {msg}") from None

    session_data = _extract_session(client, handle)
    _SESSION_CACHE[handle] = session_data
    return session_data


def get_authenticated_client(*, _client: Any = None) -> Any:
    """Return a cached, authenticated atproto Client.

    Calls create_session() if no client is cached yet.  Callers MUST NOT
    call login() again on the returned client; it is already authenticated.

    Parameters
    ----------
    _client:
        Seam for tests.
    """
    if _client is not None:
        return _client

    handle = _read_handle()
    if handle in _CLIENT_CACHE:
        return _CLIENT_CACHE[handle]

    # Authenticate and cache
    create_session()
    return _CLIENT_CACHE[handle]


def clear_session_cache() -> None:
    """Clear the in-process session and client caches (useful for tests)."""
    _SESSION_CACHE.clear()
    _CLIENT_CACHE.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_session(client: Any, handle: str) -> dict[str, Any]:
    """Extract a plain dict from an authenticated atproto Client."""
    session = client._session  # noqa: SLF001 — atproto private attr, stable across 0.0.x
    if session is None:
        raise BlueskyAuthError(f"atproto Client has no session after login for {handle!r}.")
    return {
        "handle": getattr(session, "handle", handle),
        "did": getattr(session, "did", ""),
        "accessJwt": getattr(session, "access_jwt", ""),
        "refreshJwt": getattr(session, "refresh_jwt", ""),
    }
