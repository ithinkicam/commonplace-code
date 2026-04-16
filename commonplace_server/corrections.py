"""Correction logic for the Commonplace MCP server.

Pure functions — filesystem only, no DB dependency.  Atomic writes
(tmp + fsync + rename) per the v5 durability requirement.

Public API
----------
correct_profile(correction, profile_dir)  -> dict
correct_book(slug, correction, books_dir) -> dict
"""

from __future__ import annotations

import contextlib
import datetime
import os
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_DEFAULT_PROFILE_DIR = "~/commonplace/profile/"
_DEFAULT_BOOKS_DIR = "~/commonplace/books/"
_DEFAULT_JUDGE_DIRECTIVES_PATH = "~/commonplace/skills/judge_serendipity/directives.md"

_CORRECTIONS_SECTION = "## Corrections"
_NOTES_CORRECTIONS_SECTION = "## Corrections"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.date.today().isoformat()


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically using tmp + fsync + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    os.replace(tmp_path, path)


def _build_directive_line(correction: str, date_iso: str) -> str:
    """Return a formatted directive line."""
    return f"[directive, {date_iso}] {correction}"


# ---------------------------------------------------------------------------
# Profile correction
# ---------------------------------------------------------------------------


def correct_profile(
    correction: str,
    profile_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Append a correction directive to ~/commonplace/profile/current.md.

    If *current.md* does not exist, creates it with a minimal header.
    Appends under a ``## Corrections`` section at the end of the file.

    Parameters
    ----------
    correction:
        Free-text correction string.
    profile_dir:
        Override the profile directory (defaults to the value of
        ``COMMONPLACE_PROFILE_DIR`` env var, or ``~/commonplace/profile/``).

    Returns
    -------
    dict with keys ``status``, ``target_type``, ``appended_directive``,
    ``path``.
    """
    if not correction or not correction.strip():
        return {
            "status": "error",
            "error": "correction must be non-empty",
        }

    correction = correction.strip()

    if profile_dir is None:
        raw = os.environ.get("COMMONPLACE_PROFILE_DIR", _DEFAULT_PROFILE_DIR)
        profile_dir = Path(raw).expanduser()
    else:
        profile_dir = Path(profile_dir).expanduser()

    current_md = profile_dir / "current.md"
    date_iso = _today_iso()
    directive_line = _build_directive_line(correction, date_iso)

    if current_md.exists():
        existing = current_md.read_text(encoding="utf-8")
    else:
        # Create a minimal profile file with standard header
        existing = (
            "# Profile\n\n"
            "## How to talk to me\n\n"
            "## What I'm sensitive about\n\n"
            "## How I think\n\n"
        )

    # Append under ## Corrections section (or add it if absent)
    if _CORRECTIONS_SECTION in existing:
        # Add the new directive after the section heading
        # Find the end of the corrections section (append before next ## or EOF)
        lines = existing.splitlines(keepends=True)
        insert_pos = len(lines)
        in_corrections = False
        for i, line in enumerate(lines):
            if line.strip() == _CORRECTIONS_SECTION:
                in_corrections = True
                continue
            if in_corrections and line.startswith("## "):
                insert_pos = i
                break
        # Insert the directive line before insert_pos
        new_directive_block = directive_line + "\n"
        lines.insert(insert_pos, new_directive_block)
        new_content = "".join(lines)
    else:
        # Append a new Corrections section at the end
        trailer = "" if existing.endswith("\n") else "\n"
        new_content = existing + trailer + _CORRECTIONS_SECTION + "\n\n" + directive_line + "\n"

    _atomic_write(current_md, new_content)

    return {
        "status": "applied",
        "target_type": "profile",
        "appended_directive": directive_line,
        "path": str(current_md),
    }


# ---------------------------------------------------------------------------
# Book correction
# ---------------------------------------------------------------------------


def correct_book(
    slug: str,
    correction: str,
    books_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Append a correction to a book's corrections.md (and notes.md if present).

    Parameters
    ----------
    slug:
        The book slug (directory name under ``~/commonplace/books/``).
    correction:
        Free-text correction string.
    books_dir:
        Override the books root directory (defaults to
        ``~/commonplace/books/``).

    Returns
    -------
    On success: dict with keys ``status``, ``target_type``, ``target_id``,
    ``path`` (path to corrections.md).
    On error: dict with keys ``status``, ``error``, ``target_id``.
    """
    if not slug or not slug.strip():
        return {
            "status": "error",
            "error": "slug must be non-empty",
            "target_id": slug,
        }
    if not correction or not correction.strip():
        return {
            "status": "error",
            "error": "correction must be non-empty",
            "target_id": slug,
        }

    slug = slug.strip()
    correction = correction.strip()

    if books_dir is None:
        books_dir = Path(_DEFAULT_BOOKS_DIR).expanduser()
    else:
        books_dir = Path(books_dir).expanduser()

    book_dir = books_dir / slug

    # Book directory must already exist
    if not book_dir.exists() or not book_dir.is_dir():
        return {
            "status": "error",
            "error": "book slug not found",
            "target_id": slug,
        }

    date_iso = _today_iso()
    directive_line = _build_directive_line(correction, date_iso)

    # --- corrections.md ---
    corrections_md = book_dir / "corrections.md"
    if corrections_md.exists():
        existing = corrections_md.read_text(encoding="utf-8")
    else:
        existing = "# Corrections\n\n"

    trailer = "" if existing.endswith("\n") else "\n"
    new_corrections = existing + trailer + directive_line + "\n"
    _atomic_write(corrections_md, new_corrections)

    # --- notes.md (optional update) ---
    notes_md = book_dir / "notes.md"
    if notes_md.exists():
        notes_content = notes_md.read_text(encoding="utf-8")
        if _NOTES_CORRECTIONS_SECTION in notes_content:
            # Append after the last line of the Corrections section
            lines = notes_content.splitlines(keepends=True)
            insert_pos = len(lines)
            in_corrections = False
            for i, line in enumerate(lines):
                if line.strip() == _NOTES_CORRECTIONS_SECTION:
                    in_corrections = True
                    continue
                if in_corrections and line.startswith("## "):
                    insert_pos = i
                    break
            new_directive_block = directive_line + "\n"
            lines.insert(insert_pos, new_directive_block)
            new_notes = "".join(lines)
        else:
            trailer = "" if notes_content.endswith("\n") else "\n"
            new_notes = (
                notes_content
                + trailer
                + _NOTES_CORRECTIONS_SECTION
                + "\n\n"
                + directive_line
                + "\n"
            )
        _atomic_write(notes_md, new_notes)

    return {
        "status": "applied",
        "target_type": "book",
        "target_id": slug,
        "path": str(corrections_md),
    }


# ---------------------------------------------------------------------------
# Judge serendipity correction
# ---------------------------------------------------------------------------


def correct_judge(
    correction: str,
    directives_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a directive to the judge_serendipity directives file.

    The surface tool reads this file and passes its contents to the judge as
    ``accumulated_directives``, letting the user steer surfacing behavior over
    time (e.g. "stop surfacing politics during work hours").

    Parameters
    ----------
    correction:
        Free-text directive string.
    directives_path:
        Override the directives file path (defaults to the value of
        ``COMMONPLACE_JUDGE_DIRECTIVES_PATH`` env var, or
        ``~/commonplace/skills/judge_serendipity/directives.md``).

    Returns
    -------
    dict with keys ``status``, ``target_type``, ``appended_directive``,
    ``path``.
    """
    if not correction or not correction.strip():
        return {
            "status": "error",
            "error": "correction must be non-empty",
        }

    correction = correction.strip()

    if directives_path is None:
        raw = os.environ.get(
            "COMMONPLACE_JUDGE_DIRECTIVES_PATH", _DEFAULT_JUDGE_DIRECTIVES_PATH
        )
        directives_path = Path(raw).expanduser()
    else:
        directives_path = Path(directives_path).expanduser()

    date_iso = _today_iso()
    directive_line = _build_directive_line(correction, date_iso)

    if directives_path.exists():
        existing = directives_path.read_text(encoding="utf-8")
        trailer = "" if existing.endswith("\n") else "\n"
        new_content = existing + trailer + directive_line + "\n"
    else:
        new_content = directive_line + "\n"

    _atomic_write(directives_path, new_content)

    return {
        "status": "applied",
        "target_type": "judge_serendipity",
        "appended_directive": directive_line,
        "path": str(directives_path),
    }
