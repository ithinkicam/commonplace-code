"""External binary resolution for worker handlers.

Centralises the "find ``yt-dlp`` / ``ffmpeg`` / similar" logic so handlers
don't each reinvent it. Callers should pass the return value as
``argv[0]`` to ``subprocess.run`` — the caller still owns arg construction.

Resolution order for each binary is the same shape:

  1. ``$COMMONPLACE_<NAME>_BIN`` env override (operator / test escape hatch).
  2. A venv-local copy next to the current Python interpreter. pip-installing
     a tool into the project venv pins it to our Python version, which
     matters for yt-dlp — brew's bundled python@3.14 has broken ``expat``
     on macOS, so brew's yt-dlp fails on any XML/RSS-parsing path.
  3. ``shutil.which`` on PATH (what most users have).
  4. A well-known Homebrew fallback path, as a last-resort belt-and-braces.

Returning a string is intentional; callers already accept string or Path
and the shell-quoting invariants are clearer as a str.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _resolve(
    name: str, env_var: str, homebrew_fallbacks: tuple[str, ...]
) -> str:
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit
    venv_candidate = Path(sys.executable).parent / name
    if venv_candidate.is_file() and os.access(venv_candidate, os.X_OK):
        return str(venv_candidate)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    for fallback in homebrew_fallbacks:
        if Path(fallback).is_file():
            return fallback
    return name  # let subprocess raise FileNotFoundError if truly absent


def resolve_ytdlp() -> str:
    """Return an absolute path to a working yt-dlp."""
    return _resolve(
        "yt-dlp",
        env_var="COMMONPLACE_YTDLP_BIN",
        homebrew_fallbacks=(
            "/opt/homebrew/bin/yt-dlp",
            "/usr/local/bin/yt-dlp",
        ),
    )


def resolve_ffmpeg() -> str:
    """Return an absolute path to ffmpeg."""
    return _resolve(
        "ffmpeg",
        env_var="COMMONPLACE_FFMPEG_BIN",
        homebrew_fallbacks=(
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
        ),
    )
