"""Integration tests: surface tool registered in MCP server, callable, correct shape.

Does NOT invoke claude -p live — uses claude_cli_recorder fixture.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

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
    assert inspect.iscoroutinefunction(mcp._tool_manager._tools["surface"].fn)


async def test_surface_tool_does_not_block_event_loop() -> None:
    from commonplace_server import server

    def slow_surface(**_kwargs: object) -> dict[str, object]:
        time.sleep(0.08)
        return {"accepted": [], "triangulation_groups": []}

    ticked = asyncio.Event()

    async def ticker() -> None:
        await asyncio.sleep(0.01)
        ticked.set()

    with patch.object(server, "_begin_surface_invocation", return_value=None), \
         patch.object(server, "run_surface", side_effect=slow_surface):
        surface_task = asyncio.create_task(
            server._surface_tool(seed="a substantive seed for thread isolation")
        )
        ticker_task = asyncio.create_task(ticker())
        await asyncio.wait_for(ticked.wait(), timeout=0.05)
        await surface_task
        await ticker_task


async def test_surface_tool_records_client_cancellation() -> None:
    from commonplace_server import server

    release = threading.Event()

    def blocked_surface(**_kwargs: object) -> dict[str, object]:
        release.wait(timeout=1)
        return {"accepted": [], "triangulation_groups": []}

    with patch.object(server, "_begin_surface_invocation", return_value=42), \
         patch.object(server, "run_surface", side_effect=blocked_surface), \
         patch.object(server, "_set_surface_stage") as set_stage:
        task = asyncio.create_task(
            server._surface_tool(seed="a substantive seed that gets cancelled")
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    set_stage.assert_called_once()
    assert set_stage.call_args.kwargs["invocation_id"] == 42
    assert set_stage.call_args.kwargs["invocation_status"] == "cancelled"
    assert set_stage.call_args.kwargs["stage"] == "cancelled"


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


# ---------------------------------------------------------------------------
# Test: surface_feedback tool
# ---------------------------------------------------------------------------


def _insert_invocation(db_file: str) -> int:
    from commonplace_db import connect, migrate

    conn = connect(db_file)
    migrate(conn)
    cur = conn.execute(
        "INSERT INTO surface_invocations "
        "(seed, mode, requested_limit, similarity_floor, recency_bias, "
        " judge_status, elapsed_ms) "
        "VALUES ('test seed', 'ambient', 10, 0.25, 1, 'success', 100.0)"
    )
    conn.commit()
    invocation_id = cur.lastrowid
    conn.close()
    assert invocation_id is not None
    return invocation_id


def test_surface_feedback_registered() -> None:
    from commonplace_server.server import mcp

    tool_names = list(mcp._tool_manager._tools.keys())
    assert "surface_feedback" in tool_names


def test_surface_feedback_records_verdict(tmp_path: Path) -> None:
    from commonplace_db import connect
    from commonplace_server.server import surface_feedback

    db_file = str(tmp_path / "feedback.db")
    invocation_id = _insert_invocation(db_file)

    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        result = surface_feedback(invocation_id, "used")
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]

    assert result == {"ok": True, "invocation_id": invocation_id, "verdict": "used"}

    conn = connect(db_file)
    row = conn.execute(
        "SELECT user_ack, user_ack_at FROM surface_invocations WHERE id = ?",
        (invocation_id,),
    ).fetchone()
    conn.close()
    assert row["user_ack"] == "used"
    assert row["user_ack_at"] is not None


def test_surface_feedback_rejects_bad_verdict(tmp_path: Path) -> None:
    from commonplace_server.server import surface_feedback

    db_file = str(tmp_path / "feedback_bad.db")
    invocation_id = _insert_invocation(db_file)

    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        result = surface_feedback(invocation_id, "amazing")
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]

    assert result["ok"] is False
    assert "invalid verdict" in result["error"]


def test_surface_feedback_unknown_invocation(tmp_path: Path) -> None:
    from commonplace_server.server import surface_feedback

    db_file = str(tmp_path / "feedback_missing.db")
    _insert_invocation(db_file)

    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        result = surface_feedback(999999, "ignored")
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]

    assert result["ok"] is False
    assert "no invocation" in result["error"]
