"""Shared pytest fixtures. Phase 0.0 skeletons; later phases flesh these out."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    """A throwaway vault root mirroring the ~/commonplace/ layout."""
    for sub in ("books", "captures", "bluesky", "profile", "skills"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def memory_db() -> Iterator[sqlite3.Connection]:
    """In-memory SQLite connection. WAL-like settings not applicable; good enough for unit tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@dataclass
class ClaudeCliRecorder:
    """Recorder fixture for mocking ``claude -p`` subprocess calls.

    Usage in tests::

        def test_foo(claude_cli_recorder):
            claude_cli_recorder.set_response("judge output here")
            result = run_surface(seed="some text", ...)
            assert claude_cli_recorder.calls  # verify it was called

    Attributes
    ----------
    calls:
        List of argv lists captured (each call appends one entry).
    responses:
        Queue of canned stdout strings. Consumed in FIFO order.
        If empty, returns the ``default_response``.
    default_response:
        Returned when ``responses`` queue is exhausted (default: empty JSON judgment).
    """

    calls: list[list[str]] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    default_response: str = (
        '{"accepted": [], "rejected": [], "triangulation_groups": []}'
    )
    _side_effect: Callable[[], str] | None = field(default=None, repr=False)

    def set_response(self, response: str) -> None:
        """Queue a single response string."""
        self.responses.append(response)

    def set_responses(self, responses: list[str]) -> None:
        """Queue multiple response strings (consumed FIFO)."""
        self.responses.extend(responses)

    def set_timeout(self) -> None:
        """Make the next call raise subprocess.TimeoutExpired."""
        import subprocess

        def _raise() -> str:
            raise subprocess.TimeoutExpired(cmd=["claude"], timeout=30)

        self._side_effect = _raise


@pytest.fixture
def claude_cli_recorder(monkeypatch: pytest.MonkeyPatch) -> ClaudeCliRecorder:
    """Record and stub ``subprocess.run`` calls to ``claude -p``.

    Returns a :class:`ClaudeCliRecorder` whose ``.calls`` list is populated
    for every subprocess.run invocation and whose ``.responses`` queue
    supplies canned stdout strings (or raises TimeoutExpired if ``set_timeout``
    was called).
    """
    recorder = ClaudeCliRecorder()

    def _fake_run(
        cmd: list[str], *args: object, **kwargs: object
    ) -> object:
        recorder.calls.append(list(cmd))

        if recorder._side_effect is not None:
            fn = recorder._side_effect
            recorder._side_effect = None
            fn()  # raises if set_timeout was called

        stdout = recorder.responses.pop(0) if recorder.responses else recorder.default_response

        class _Result:
            returncode = 0
            stderr = ""

        _Result.stdout = stdout  # type: ignore[attr-defined]
        return _Result()

    import subprocess

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return recorder


@pytest.fixture
def fake_claude_cli(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record `claude -p` invocations without actually running the subprocess.

    Returns the list of captured argv lists. Later phases will extend this to
    return canned skill outputs keyed on the skill name.
    """
    captured: list[list[str]] = []

    def fake_run(argv: list[str], *_args: object, **_kwargs: object) -> object:
        captured.append(list(argv))

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured
