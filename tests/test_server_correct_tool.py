"""Integration tests for the `correct` MCP tool registered in server.py.

Verifies the tool is registered, callable in-process, and returns the
expected return shape for profile, book, and error cases.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_correct(
    target_type: str,
    correction: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Import and invoke the correct() function directly (no live server)."""
    from commonplace_server.server import correct  # noqa: PLC0415

    return correct(target_type=target_type, correction=correction, target_id=target_id)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_correct_tool_is_registered() -> None:
    """The `correct` function should be importable from server module."""
    from commonplace_server.server import correct, mcp  # noqa: PLC0415

    assert callable(correct)
    # FastMCP keeps a registry in _tool_manager._tools keyed by tool name
    tool_names = set(mcp._tool_manager._tools.keys())  # type: ignore[attr-defined]
    assert "correct" in tool_names, f"'correct' not found in tool registry: {tool_names}"


# ---------------------------------------------------------------------------
# Profile — happy path
# ---------------------------------------------------------------------------


def test_correct_profile_returns_applied(tmp_path: Path) -> None:
    """correct('profile', ...) should return status='applied'."""
    profile_dir = tmp_path / "profile"
    os.environ["COMMONPLACE_PROFILE_DIR"] = str(profile_dir)
    try:
        result = _call_correct("profile", "prefer blunt register")
        assert result["status"] == "applied"
        assert result["target_type"] == "profile"
        assert "appended_directive" in result
        assert "path" in result
    finally:
        del os.environ["COMMONPLACE_PROFILE_DIR"]


def test_correct_profile_creates_current_md(tmp_path: Path) -> None:
    """correct('profile', ...) should create current.md when absent."""
    profile_dir = tmp_path / "profile"
    os.environ["COMMONPLACE_PROFILE_DIR"] = str(profile_dir)
    try:
        _call_correct("profile", "test directive")
        assert (profile_dir / "current.md").exists()
    finally:
        del os.environ["COMMONPLACE_PROFILE_DIR"]


def test_correct_profile_directive_in_file(tmp_path: Path) -> None:
    """The directive text and date should appear in current.md."""
    profile_dir = tmp_path / "profile"
    os.environ["COMMONPLACE_PROFILE_DIR"] = str(profile_dir)
    try:
        correction = "blunt register preferred"
        result = _call_correct("profile", correction)
        content = (profile_dir / "current.md").read_text()
        assert correction in content
        assert result["appended_directive"] in content
    finally:
        del os.environ["COMMONPLACE_PROFILE_DIR"]


def test_correct_profile_ignores_target_id(tmp_path: Path) -> None:
    """For profile corrections, target_id should be ignored without error."""
    profile_dir = tmp_path / "profile"
    os.environ["COMMONPLACE_PROFILE_DIR"] = str(profile_dir)
    try:
        result = _call_correct("profile", "correction text", target_id="some-id")
        assert result["status"] == "applied"
    finally:
        del os.environ["COMMONPLACE_PROFILE_DIR"]


# ---------------------------------------------------------------------------
# Book — happy path
# ---------------------------------------------------------------------------


def test_correct_book_returns_applied(tmp_path: Path) -> None:
    """correct('book', ...) should return status='applied' for existing slug."""
    books_dir = tmp_path / "books"
    slug = "the-book-slug"
    (books_dir / slug).mkdir(parents=True)

    # Patch books dir via env var (corrections.py reads COMMONPLACE_BOOKS_DIR if set)
    # We pass books_dir directly via the underlying function but the server uses defaults.
    # Instead, test via the underlying correct_book to avoid env var coupling.
    from commonplace_server.corrections import correct_book  # noqa: PLC0415

    result = correct_book(slug, "this is a memoir", books_dir=books_dir)
    assert result["status"] == "applied"
    assert result["target_type"] == "book"
    assert result["target_id"] == slug
    assert "path" in result


def test_correct_book_corrections_md_present(tmp_path: Path) -> None:
    """corrections.md should be created with the correction content."""
    books_dir = tmp_path / "books"
    slug = "corrections-test-book"
    (books_dir / slug).mkdir(parents=True)

    from commonplace_server.corrections import correct_book  # noqa: PLC0415

    correct_book(slug, "genre correction", books_dir=books_dir)

    corrections_md = books_dir / slug / "corrections.md"
    assert corrections_md.exists()
    assert "genre correction" in corrections_md.read_text()


# ---------------------------------------------------------------------------
# Book — slug not found
# ---------------------------------------------------------------------------


def test_correct_book_slug_not_found_returns_error(tmp_path: Path) -> None:
    """When slug directory doesn't exist, return error dict."""
    books_dir = tmp_path / "books"
    books_dir.mkdir(parents=True)

    from commonplace_server.corrections import correct_book  # noqa: PLC0415

    result = correct_book("no-such-slug", "correction", books_dir=books_dir)
    assert result["status"] == "error"
    assert result["error"] == "book slug not found"
    assert result["target_id"] == "no-such-slug"


def test_correct_book_missing_target_id_returns_error(tmp_path: Path) -> None:
    """Calling correct('book', ...) without target_id should return error."""
    result = _call_correct("book", "some correction", target_id=None)
    assert result["status"] == "error"
    assert "target_id" in result["error"]


def test_correct_book_empty_target_id_returns_error() -> None:
    """Calling correct('book', ...) with empty string target_id should error."""
    result = _call_correct("book", "some correction", target_id="")
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Unknown target_type
# ---------------------------------------------------------------------------


def test_correct_unknown_target_type_returns_error() -> None:
    """Unknown target_type should return error dict with clear message."""
    result = _call_correct("journal", "some correction")
    assert result["status"] == "error"
    assert "target_type" in result["error"] or "journal" in result["error"]


# ---------------------------------------------------------------------------
# Return shape completeness
# ---------------------------------------------------------------------------


def test_correct_profile_return_shape(tmp_path: Path) -> None:
    """Profile result dict must contain all required keys."""
    profile_dir = tmp_path / "profile"
    os.environ["COMMONPLACE_PROFILE_DIR"] = str(profile_dir)
    try:
        result = _call_correct("profile", "shape test")
        assert {"status", "target_type", "appended_directive", "path"} <= set(result.keys())
    finally:
        del os.environ["COMMONPLACE_PROFILE_DIR"]
