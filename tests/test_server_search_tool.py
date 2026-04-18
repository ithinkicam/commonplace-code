"""Smoke tests for the ``search_commonplace`` MCP tool wrapper in server.py.

Verifies that:
- The tool is registered with FastMCP.
- New liturgical filter parameters are accepted by the wrapper.
- The wrapper returns the expected ``{"results": [...], "count": <int>}`` shape
  (or an error key on embedding failure) without raising.

Uses a fake embedder (monkeypatch) so no live Ollama is required.
"""

from __future__ import annotations

import struct
from typing import Any

_DIM = 768


def _zero_blob() -> bytes:
    """Return a 768-dim zero-vector as packed float32."""
    return struct.pack(f"<{_DIM}f", *([0.0] * _DIM))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_search(
    tmp_path: Any,
    monkeypatch: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Call search_commonplace with a fake embedder and an in-process DB."""
    db_file = str(tmp_path / "search_smoke.db")
    monkeypatch.setenv("COMMONPLACE_DB_PATH", db_file)

    # Patch embed() to return a single zero-vector without calling Ollama.
    monkeypatch.setattr(
        "commonplace_server.server.search_commonplace_impl",
        _fake_search_impl,
    )

    import commonplace_server.embedding as emb  # noqa: PLC0415
    from commonplace_server.server import search_commonplace  # noqa: PLC0415

    # Patch embed so the wrapper doesn't fail before reaching our fake impl.
    monkeypatch.setattr(emb, "embed", lambda texts: [[0.0] * _DIM])

    return search_commonplace(query="grace", **kwargs)


def _fake_search_impl(*_args: Any, **_kwargs: Any) -> list[Any]:
    """Stub impl that always returns an empty list (no DB needed)."""
    return []


# ---------------------------------------------------------------------------
# Registration check
# ---------------------------------------------------------------------------


def test_search_commonplace_is_registered() -> None:
    """search_commonplace must be importable and registered with FastMCP."""
    from commonplace_server.server import mcp, search_commonplace  # noqa: PLC0415

    assert callable(search_commonplace)
    tool_names = set(mcp._tool_manager._tools.keys())  # type: ignore[attr-defined]
    assert "search_commonplace" in tool_names, (
        f"'search_commonplace' not in tool registry: {tool_names}"
    )


# ---------------------------------------------------------------------------
# Smoke: new liturgical params accepted without error
# ---------------------------------------------------------------------------


def test_search_accepts_category_param(tmp_path: Any, monkeypatch: Any) -> None:
    """Passing category= should not raise and should return the standard shape."""
    result = _call_search(tmp_path, monkeypatch, category="liturgical_proper")
    assert "results" in result
    assert "count" in result


def test_search_accepts_genre_param(tmp_path: Any, monkeypatch: Any) -> None:
    """Passing genre= should not raise and should return the standard shape."""
    result = _call_search(tmp_path, monkeypatch, genre="collect")
    assert "results" in result
    assert "count" in result


def test_search_accepts_tradition_param(tmp_path: Any, monkeypatch: Any) -> None:
    """Passing tradition= should not raise and should return the standard shape."""
    result = _call_search(tmp_path, monkeypatch, tradition="anglican")
    assert "results" in result
    assert "count" in result


def test_search_accepts_feast_name_param(tmp_path: Any, monkeypatch: Any) -> None:
    """Passing feast_name= should not raise and should return the standard shape."""
    result = _call_search(tmp_path, monkeypatch, feast_name="Easter")
    assert "results" in result
    assert "count" in result


def test_search_accepts_calendar_year_param(tmp_path: Any, monkeypatch: Any) -> None:
    """Passing calendar_year= should not raise and should return the standard shape."""
    result = _call_search(
        tmp_path,
        monkeypatch,
        content_type="liturgical_unit",
        date_from="2026-04-01",
        date_to="2026-04-30",
        calendar_year=2026,
    )
    assert "results" in result
    assert "count" in result


def test_search_accepts_all_new_params_combined(tmp_path: Any, monkeypatch: Any) -> None:
    """All five new params together should not raise."""
    result = _call_search(
        tmp_path,
        monkeypatch,
        content_type="liturgical_unit",
        category="liturgical_proper",
        genre="collect",
        tradition="anglican",
        feast_name="Easter",
        calendar_year=2026,
    )
    assert "results" in result
    assert "count" in result


# ---------------------------------------------------------------------------
# Backward compat: old params still work
# ---------------------------------------------------------------------------


def test_search_backward_compat_old_params(tmp_path: Any, monkeypatch: Any) -> None:
    """Calling with only pre-existing params must still work normally."""
    result = _call_search(
        tmp_path,
        monkeypatch,
        content_type="book",
        source="example.com",
        date_from="2025-01-01",
        date_to="2025-12-31",
        limit=5,
    )
    assert "results" in result
    assert "count" in result


def test_search_empty_query_returns_error(tmp_path: Any, monkeypatch: Any) -> None:
    """An empty query must return an error dict without raising."""
    db_file = str(tmp_path / "empty_q.db")
    monkeypatch.setenv("COMMONPLACE_DB_PATH", db_file)

    from commonplace_server.server import search_commonplace  # noqa: PLC0415

    result = search_commonplace(query="   ")
    assert result.get("error") is not None
    assert result["count"] == 0
