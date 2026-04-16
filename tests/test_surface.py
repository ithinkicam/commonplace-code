"""Tests for commonplace_server.surface — the serendipity surface tool.

Does NOT invoke claude -p live — uses claude_cli_recorder fixture.

Covers:
  - empty seed
  - no candidates above floor (nothing passes similarity_floor)
  - judge rejects all
  - judge accepts 1
  - judge accepts 2 with triangulation
  - judge parse fails silently (garbage output)
  - judge timeout (fail silently)
  - judge emits code fences (strip_code_fences integration)
  - types filter
  - recency_bias on/off
  - accumulated_directives loaded when present
  - accumulated_directives empty when missing
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import struct
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from commonplace_db import connect, migrate

REPO_ROOT = Path(__file__).parent.parent
PARSER_PATH = REPO_ROOT / "skills" / "judge_serendipity" / "parser.py"

# Load parser for building canned judge outputs in tests.
_spec = importlib.util.spec_from_file_location("surface_judge_parser", PARSER_PATH)
assert _spec is not None and _spec.loader is not None
if "surface_judge_parser" not in sys.modules:
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["surface_judge_parser"] = _mod
    _spec.loader.exec_module(_mod)

_DIM = 768


# ---------------------------------------------------------------------------
# DB helpers (mirrors test_search.py pattern)
# ---------------------------------------------------------------------------


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _make_vec(val: float) -> list[float]:
    return [val] * _DIM


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _insert_doc(
    conn: sqlite3.Connection,
    content_type: str = "capture",
    title: str = "Test Doc",
    source_uri: str | None = None,
    created_at: str = "2025-01-01T00:00:00Z",
) -> int:
    cur = conn.execute(
        "INSERT INTO documents (content_type, title, source_uri, created_at) "
        "VALUES (?, ?, ?, ?)",
        (content_type, title, source_uri, created_at),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _insert_chunk_with_embedding(
    conn: sqlite3.Connection,
    document_id: int,
    text: str,
    vec: list[float],
    chunk_index: int = 0,
) -> int:
    cur = conn.execute(
        "INSERT INTO chunks (document_id, chunk_index, text, token_count) VALUES (?, ?, ?, ?)",
        (document_id, chunk_index, text, len(text.split())),
    )
    conn.commit()
    chunk_id = cur.lastrowid
    blob = _pack(vec)
    conn.execute(
        "INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, blob),
    )
    conn.commit()
    return chunk_id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Judge output builders
# ---------------------------------------------------------------------------


def _judge_accept(candidate_id: str, reason: str = "genuine connection") -> str:
    return json.dumps(
        {
            "accepted": [{"id": candidate_id, "reason": reason}],
            "rejected": [],
            "triangulation_groups": [],
        }
    )


def _judge_reject(candidate_id: str, reason: str = "thematic-only: shallow") -> str:
    return json.dumps(
        {
            "accepted": [],
            "rejected": [{"id": candidate_id, "reason": reason}],
            "triangulation_groups": [],
        }
    )


def _judge_reject_all(candidate_ids: list[str]) -> str:
    return json.dumps(
        {
            "accepted": [],
            "rejected": [{"id": cid, "reason": "thematic-only: shallow"} for cid in candidate_ids],
            "triangulation_groups": [],
        }
    )


def _judge_accept_and_triangulate(
    accepted_id: str,
    tri_ids: list[str],
    tri_reason: str = "three traditions, different corners",
) -> str:
    return json.dumps(
        {
            "accepted": [{"id": accepted_id, "reason": "genuine connective claim"}],
            "rejected": [],
            "triangulation_groups": [
                {"ids": tri_ids, "reason": tri_reason}
            ],
        }
    )


def _judge_triangulate(tri_ids: list[str], tri_reason: str, rejected_ids: list[str]) -> str:
    return json.dumps(
        {
            "accepted": [],
            "rejected": [{"id": rid, "reason": "thematic-only"} for rid in rejected_ids],
            "triangulation_groups": [
                {"ids": tri_ids, "reason": tri_reason}
            ],
        }
    )


# ---------------------------------------------------------------------------
# Fake embedding (always returns distance~0 so similarity~1.0)
# ---------------------------------------------------------------------------

_ZERO_VEC = [0.0] * _DIM
_CLOSE_VEC = [1.0] + [0.0] * (_DIM - 1)


def _fake_embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    """Return a fixed embedding that matches _CLOSE_VEC closely."""
    return [_CLOSE_VEC for _ in texts]


def _fake_pack_vector(vec: list[float]) -> bytes:
    return _pack(vec)


# ---------------------------------------------------------------------------
# Helper: run surface with a mocked DB and embedding
# ---------------------------------------------------------------------------


def _run_surface_with_db(
    conn: sqlite3.Connection,
    seed: str,
    mode: str = "ambient",
    types: list[str] | None = None,
    limit: int = 10,
    similarity_floor: float = 0.0,  # set to 0.0 so tests can control floor easily
    recency_bias: bool = True,
    directives_path: Path | None = None,
) -> dict[str, Any]:
    """Call run_surface with mocked DB path and embedding."""
    from commonplace_server import surface as surface_mod

    orig_embed = surface_mod.embed
    orig_pack = surface_mod.pack_vector
    orig_directives = surface_mod.DIRECTIVES_PATH

    surface_mod.embed = _fake_embed  # type: ignore[assignment]
    surface_mod.pack_vector = _fake_pack_vector  # type: ignore[assignment]
    if directives_path is not None:
        surface_mod.DIRECTIVES_PATH = directives_path  # type: ignore[assignment]

    try:
        # Patch commonplace_db.connect to return our test connection
        with patch("commonplace_server.surface.commonplace_db") as mock_db:
            mock_db.DB_PATH = ":memory:"
            mock_db.connect.return_value = conn
            mock_db.migrate.return_value = 1

            result = surface_mod.run_surface(
                seed=seed,
                mode=mode,
                types=types,
                limit=limit,
                similarity_floor=similarity_floor,
                recency_bias=recency_bias,
                db_path=":memory:",
            )
    finally:
        surface_mod.embed = orig_embed  # type: ignore[assignment]
        surface_mod.pack_vector = orig_pack  # type: ignore[assignment]
        if directives_path is not None:
            surface_mod.DIRECTIVES_PATH = orig_directives  # type: ignore[assignment]

    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptySeed:
    def test_empty_string(self, claude_cli_recorder: Any) -> None:
        from commonplace_server.surface import run_surface

        result = run_surface(seed="")
        assert result == {"accepted": [], "triangulation_groups": [], "note": "empty seed"}
        assert not claude_cli_recorder.calls  # judge never called

    def test_whitespace_only(self, claude_cli_recorder: Any) -> None:
        from commonplace_server.surface import run_surface

        result = run_surface(seed="   \n  ")
        assert result == {"accepted": [], "triangulation_groups": [], "note": "empty seed"}
        assert not claude_cli_recorder.calls


class TestNoCandidatesAboveFloor:
    def test_floor_drops_all(self, db: sqlite3.Connection, claude_cli_recorder: Any) -> None:
        """When similarity floor is very high, no candidates pass."""
        doc_id = _insert_doc(db, title="Philosophy Notes")
        # Insert a vector far from query — large distance → low similarity
        far_vec = [-1.0] + [0.0] * (_DIM - 1)
        _insert_chunk_with_embedding(db, doc_id, "some text", far_vec)

        result = _run_surface_with_db(
            db,
            seed="divine hiddenness and presence",
            similarity_floor=0.99,  # unreachable floor
        )
        assert result["accepted"] == []
        assert result["triangulation_groups"] == []
        assert "similarity floor" in result.get("note", "")
        assert not claude_cli_recorder.calls  # judge never invoked

    def test_empty_db_returns_floor_note(self, db: sqlite3.Connection, claude_cli_recorder: Any) -> None:
        """Empty DB → no results → floor note."""
        result = _run_surface_with_db(db, seed="some substantive seed")
        assert result["accepted"] == []
        assert not claude_cli_recorder.calls


class TestJudgeRejectsAll:
    def test_judge_rejects_all_candidates(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Weil Book")
        close_vec = _CLOSE_VEC
        _insert_chunk_with_embedding(db, doc_id, "attention is the highest form of prayer", close_vec)

        # Find out what candidate id will be used: it's "{document_id}:0"
        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_reject(cand_id))

        result = _run_surface_with_db(db, seed="attention and the divine", similarity_floor=0.0)
        assert result["accepted"] == []
        assert result["triangulation_groups"] == []
        assert result.get("rejected_count", 0) == 1
        assert len(claude_cli_recorder.calls) == 1


class TestJudgeAcceptsOne:
    def test_judge_accepts_single_candidate(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Weil: Gravity and Grace", source_uri="file://weil.md")
        _insert_chunk_with_embedding(
            db, doc_id, "Attention is the highest form of prayer", _CLOSE_VEC
        )

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(
            _judge_accept(cand_id, "Weil reframes hiddenness as posture requiring the seeker's attention")
        )

        result = _run_surface_with_db(db, seed="divine hiddenness and posture", similarity_floor=0.0)
        assert len(result["accepted"]) == 1
        assert result["accepted"][0]["id"] == cand_id
        assert result["accepted"][0]["source_title"] == "Weil: Gravity and Grace"
        assert result["accepted"][0]["text"] == "Attention is the highest form of prayer"
        assert "reason" in result["accepted"][0]
        assert result["triangulation_groups"] == []
        assert result["mode"] == "ambient"
        assert result["seed"] == "divine hiddenness and posture"

    def test_result_includes_similarity_score(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Test")
        _insert_chunk_with_embedding(db, doc_id, "some text", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_accept(cand_id))

        result = _run_surface_with_db(db, seed="some substantive text", similarity_floor=0.0)
        assert isinstance(result["accepted"][0]["similarity_score"], float)

    def test_result_includes_source_uri(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Test Book", source_uri="https://example.com/book")
        _insert_chunk_with_embedding(db, doc_id, "passage text", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_accept(cand_id))

        result = _run_surface_with_db(db, seed="some text", similarity_floor=0.0)
        assert result["accepted"][0]["source_uri"] == "https://example.com/book"


class TestJudgeAcceptsTwoWithTriangulation:
    def test_triangulation_group_in_result(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc1 = _insert_doc(db, title="Weil")
        doc2 = _insert_doc(db, title="Augustine")
        doc3 = _insert_doc(db, title="Plato")

        _insert_chunk_with_embedding(db, doc1, "attention text", _CLOSE_VEC, chunk_index=0)
        _insert_chunk_with_embedding(db, doc2, "augustine text", _CLOSE_VEC, chunk_index=0)
        _insert_chunk_with_embedding(db, doc3, "plato text", _CLOSE_VEC, chunk_index=0)

        cid1 = f"{doc1}:0"
        cid2 = f"{doc2}:1"
        cid3 = f"{doc3}:2"

        # Judge triangulates doc2 and doc3, rejects doc1
        claude_cli_recorder.set_response(
            _judge_triangulate(
                tri_ids=[cid2, cid3],
                tri_reason="Augustine and Plato on attention from different traditions",
                rejected_ids=[cid1],
            )
        )

        result = _run_surface_with_db(
            db, seed="attention across traditions", similarity_floor=0.0
        )
        assert result["accepted"] == []
        assert len(result["triangulation_groups"]) == 1
        group = result["triangulation_groups"][0]
        assert "reason" in group
        assert "items" in group
        assert len(group["items"]) == 2

    def test_accepted_plus_triangulation(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc1 = _insert_doc(db, title="Weil")
        doc2 = _insert_doc(db, title="Augustine")
        doc3 = _insert_doc(db, title="Plato")

        _insert_chunk_with_embedding(db, doc1, "weil text", _CLOSE_VEC, chunk_index=0)
        _insert_chunk_with_embedding(db, doc2, "augustine text", _CLOSE_VEC, chunk_index=0)
        _insert_chunk_with_embedding(db, doc3, "plato text", _CLOSE_VEC, chunk_index=0)

        cid1 = f"{doc1}:0"
        cid2 = f"{doc2}:1"
        cid3 = f"{doc3}:2"

        claude_cli_recorder.set_response(
            _judge_accept_and_triangulate(
                accepted_id=cid1,
                tri_ids=[cid2, cid3],
                tri_reason="Augustine and Plato triangulate on the question",
            )
        )

        result = _run_surface_with_db(db, seed="attention text", similarity_floor=0.0)
        assert len(result["accepted"]) == 1
        assert len(result["triangulation_groups"]) == 1
        assert result["rejected_count"] == 0


class TestJudgeParseFails:
    def test_garbage_output_returns_silently(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Some Book")
        _insert_chunk_with_embedding(db, doc_id, "some passage", _CLOSE_VEC)

        claude_cli_recorder.set_response("This is garbage, not JSON at all!!!")

        result = _run_surface_with_db(db, seed="some topic", similarity_floor=0.0)
        assert result["accepted"] == []
        assert result["triangulation_groups"] == []
        assert result.get("note") == "judge output unparseable"
        assert "raw" in result

    def test_unparseable_raw_is_truncated(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Test")
        _insert_chunk_with_embedding(db, doc_id, "text", _CLOSE_VEC)

        long_garbage = "x" * 500
        claude_cli_recorder.set_response(long_garbage)

        result = _run_surface_with_db(db, seed="seed", similarity_floor=0.0)
        assert len(result.get("raw", "")) <= 200


class TestJudgeTimeout:
    def test_timeout_returns_empty_silently(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Weil")
        _insert_chunk_with_embedding(db, doc_id, "passage text", _CLOSE_VEC)

        claude_cli_recorder.set_timeout()

        result = _run_surface_with_db(db, seed="some seed", similarity_floor=0.0)
        assert result["accepted"] == []
        assert result["triangulation_groups"] == []
        # No exception raised — fails silently
        assert "timed out" in result.get("note", "").lower() or "failed" in result.get("note", "").lower()


class TestCodeFencesStripped:
    def test_judge_emits_code_fences_still_parsed(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        """strip_code_fences must be called — fenced output still works."""
        doc_id = _insert_doc(db, title="Weil")
        _insert_chunk_with_embedding(db, doc_id, "attention text", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        raw_json = _judge_accept(cand_id, "genuine claim")
        # Wrap in code fences, as Haiku often does
        fenced_output = f"```json\n{raw_json}\n```"
        claude_cli_recorder.set_response(fenced_output)

        result = _run_surface_with_db(db, seed="attention and prayer", similarity_floor=0.0)
        # Should parse successfully despite fences
        assert len(result["accepted"]) == 1
        assert result["accepted"][0]["id"] == cand_id


class TestTypesFilter:
    def test_types_filter_includes_only_matching(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_book = _insert_doc(db, content_type="book", title="A Book")
        doc_capture = _insert_doc(db, content_type="capture", title="A Capture")

        _insert_chunk_with_embedding(db, doc_book, "book passage", _CLOSE_VEC, chunk_index=0)
        _insert_chunk_with_embedding(db, doc_capture, "capture passage", _CLOSE_VEC, chunk_index=0)

        cid_book = f"{doc_book}:0"
        # Only book candidate should reach judge; capture is filtered out
        claude_cli_recorder.set_response(_judge_accept(cid_book))

        _run_surface_with_db(
            db, seed="some text", types=["book"], similarity_floor=0.0
        )
        assert len(claude_cli_recorder.calls) == 1
        # Verify judge was only sent candidates of type "book"
        call_args = claude_cli_recorder.calls[0]
        judge_input_str = call_args[-1]  # last arg is the JSON input
        judge_input = json.loads(judge_input_str)
        for cand in judge_input["candidates"]:
            assert cand["source_type"] == "book"


class TestRecencyBias:
    def test_recency_bias_true_includes_days_ago(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Old Book", created_at="2020-01-01T00:00:00Z")
        _insert_chunk_with_embedding(db, doc_id, "old passage", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_accept(cand_id))

        _run_surface_with_db(db, seed="some text", recency_bias=True, similarity_floor=0.0)

        call_args = claude_cli_recorder.calls[0]
        judge_input = json.loads(call_args[-1])
        candidate = judge_input["candidates"][0]
        assert candidate["last_engaged_days_ago"] is not None
        assert isinstance(candidate["last_engaged_days_ago"], int)
        assert candidate["last_engaged_days_ago"] > 0

    def test_recency_bias_false_sends_null(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Book", created_at="2020-01-01T00:00:00Z")
        _insert_chunk_with_embedding(db, doc_id, "passage", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_accept(cand_id))

        _run_surface_with_db(db, seed="some text", recency_bias=False, similarity_floor=0.0)

        call_args = claude_cli_recorder.calls[0]
        judge_input = json.loads(call_args[-1])
        candidate = judge_input["candidates"][0]
        assert candidate["last_engaged_days_ago"] is None


class TestAccumulatedDirectives:
    def test_directives_loaded_when_present(
        self, db: sqlite3.Connection, claude_cli_recorder: Any, tmp_path: Path
    ) -> None:
        directives_file = tmp_path / "directives.md"
        directives_file.write_text(
            "prefer candidates that make a real connective claim\nskip Bluesky in theological discussions",
            encoding="utf-8",
        )

        doc_id = _insert_doc(db, title="Test")
        _insert_chunk_with_embedding(db, doc_id, "text", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_accept(cand_id))

        _run_surface_with_db(
            db,
            seed="some topic",
            similarity_floor=0.0,
            directives_path=directives_file,
        )

        call_args = claude_cli_recorder.calls[0]
        judge_input = json.loads(call_args[-1])
        assert len(judge_input["accumulated_directives"]) == 2
        assert "connective claim" in judge_input["accumulated_directives"][0]

    def test_directives_empty_when_missing(
        self, db: sqlite3.Connection, claude_cli_recorder: Any, tmp_path: Path
    ) -> None:
        missing_path = tmp_path / "nonexistent" / "directives.md"

        doc_id = _insert_doc(db, title="Test")
        _insert_chunk_with_embedding(db, doc_id, "text", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_accept(cand_id))

        _run_surface_with_db(
            db,
            seed="some topic",
            similarity_floor=0.0,
            directives_path=missing_path,
        )

        call_args = claude_cli_recorder.calls[0]
        judge_input = json.loads(call_args[-1])
        assert judge_input["accumulated_directives"] == []


class TestModePassedToJudge:
    def test_on_demand_mode_sent_to_judge(
        self, db: sqlite3.Connection, claude_cli_recorder: Any
    ) -> None:
        doc_id = _insert_doc(db, title="Test")
        _insert_chunk_with_embedding(db, doc_id, "text", _CLOSE_VEC)

        cand_id = f"{doc_id}:0"
        claude_cli_recorder.set_response(_judge_accept(cand_id))

        result = _run_surface_with_db(
            db, seed="some text", mode="on_demand", similarity_floor=0.0
        )

        call_args = claude_cli_recorder.calls[0]
        judge_input = json.loads(call_args[-1])
        assert judge_input["mode"] == "on_demand"
        assert result.get("mode") == "on_demand"
