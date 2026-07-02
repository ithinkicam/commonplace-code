"""Shared SIGALRM-based timeout context manager for handler subprocess work.

Used to bound blocking calls that lack a native timeout parameter (Tesseract
OCR, certain Whisper paths, Google Drive File Provider file I/O). Delivers
SIGALRM to the main thread only, so it interrupts blocking syscalls the
standard ``threading.Timer`` + ``Thread.join`` pattern cannot reach.

Limitations
-----------
* Main thread only. ``signal.signal`` raises ``ValueError`` from a non-main
  thread, which is the correct failure mode — misuse surfaces immediately
  rather than silently no-op'ing.
* POSIX only. macOS and Linux both support SIGALRM; Windows does not. The
  worker is Mac-mini-only, so this is fine.
* Does not nest reliably — the outer alarm is clobbered by the inner one.
  Callers should treat this as a leaf-level defense, not stacked with
  other alarm-based timeouts.
"""

from __future__ import annotations

import contextlib
import signal
from collections.abc import Iterator
from typing import Any


class AlarmTimeout(TimeoutError):
    """Raised when an alarm-bounded block exceeds its timeout."""


@contextlib.contextmanager
def alarm_timeout(seconds: int, message: str = "operation timed out") -> Iterator[None]:
    """Raise ``AlarmTimeout`` if the wrapped block exceeds ``seconds``.

    Restores any previously-installed SIGALRM handler on exit, so nested
    callers see their outer alarm behaviour preserved. Passing a
    non-positive ``seconds`` disables the alarm (useful for tests that
    want to opt out without changing call sites).
    """
    if seconds <= 0:
        yield
        return

    def _raise_timeout(_signum: int, _frame: Any) -> None:
        raise AlarmTimeout(f"{message} (exceeded {seconds}s)")

    previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
