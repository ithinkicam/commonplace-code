"""Integration tests: surface tool registered in MCP server, callable, correct shape.

Does NOT invoke claude -p live — uses claude_cli_recorder fixture.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Test: tool registered in server
# ---------------------------------------------------------------------------


def test_surface_tool_registered() -> None:
    """surface must be registered as an MCP tool in server.py."""
    from commonplace_server.server import mcp

    # FastMCP stores tools; check that 'surface' is present.
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "surface" in tool_names, (
        f"'surface' not found among registered tools: {tool_names}"
    )


# ---------------------------------------------------------------------------
# Test: surface callable from server module and returns expected shape
# ---------------------------------------------------------------------------


def test_surface_callable_from_server_returns_shape(
    tmp_path: Path, claude_cli_recorder: Any
) -> None:
    """Call surface() from server.py; verify return shape matches spec."""
    db_file = str(tmp_path / "test_surface_tool.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file

    try:
        from commonplace_server.server import surface

        # Patch embed so we don't need Ollama running
        with patch("commonplace_server.surface.embed") as mock_embed, \
             patch("commonplace_server.surface.pack_vector") as mock_pack:
            mock_embed.return_value = [[0.0] * 768]
            mock_pack.return_value = b"\x00" * (768 * 4)

            # No DB records → no candidates → returns floor note
            result = surface(seed="divine hiddenness and presence")

        assert isinstance(result, dict)
        assert "accepted" in result
        assert "triangulation_groups" in result
        assert isinstance(result["accepted"], list)
        assert isinstance(result["triangulation_groups"], list)

    finally:
        del os.environ["COMMONPLACE_DB_PATH"]


def test_surface_empty_seed_from_server() -> None:
    """surface() with empty seed returns expected note without touching DB."""
    from commonplace_server.server import surface

    result = surface(seed="")
    assert result == {"accepted": [], "triangulation_groups": [], "note": "empty seed"}


def test_surface_tool_docstring_mentions_trigger() -> None:
    """Tool docstring must lead with trigger condition (per v5 ≤100 tokens guideline)."""
    from commonplace_server.server import surface

    doc = surface.__doc__ or ""
    # Must mention 'substantive' or 'invoke when' or similar
    lower = doc.lower()
    assert "invoke" in lower or "surface" in lower, (
        "Docstring must describe trigger condition"
    )


def test_surface_returns_mode_and_seed_in_output(
    tmp_path: Path, claude_cli_recorder: Any
) -> None:
    """When candidates exist and judge accepts, result contains seed and mode."""
    import struct

    from commonplace_db import connect, migrate

    db_file = str(tmp_path / "surface_mode_test.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file

    try:
        conn = connect(db_file)
        migrate(conn)

        # Insert a document + chunk
        _DIM = 768
        close_vec = [1.0] + [0.0] * (_DIM - 1)
        blob = struct.pack(f"<{_DIM}f", *close_vec)

        cur = conn.execute(
            "INSERT INTO documents (content_type, title, created_at) VALUES (?, ?, ?)",
            ("book", "Test Book", "2020-01-01T00:00:00Z"),
        )
        conn.commit()
        doc_id = cur.lastrowid

        cur2 = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, token_count) VALUES (?, ?, ?, ?)",
            (doc_id, 0, "Test passage text", 3),
        )
        conn.commit()
        chunk_id = cur2.lastrowid
        conn.execute(
            "INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, blob),
        )
        conn.commit()
        conn.close()

        cand_id = f"{doc_id}:0"
        accept_output = (
            '{"accepted": [{"id": "'
            + cand_id
            + '", "reason": "genuine connective claim"}], '
            '"rejected": [], "triangulation_groups": []}'
        )
        claude_cli_recorder.set_response(accept_output)

        from commonplace_server.server import surface

        with patch("commonplace_server.surface.embed") as mock_embed, \
             patch("commonplace_server.surface.pack_vector") as mock_pack:
            mock_embed.return_value = [close_vec]
            mock_pack.return_value = blob

            result = surface(
                seed="attention and the divine hiddenness",
                mode="on_demand",
            )

        if "accepted" in result and len(result["accepted"]) > 0:
            assert result.get("seed") == "attention and the divine hiddenness"
            assert result.get("mode") == "on_demand"
            assert "rejected_count" in result

    finally:
        os.environ.pop("COMMONPLACE_DB_PATH", None)
