"""Tests for commonplace_worker/handlers/video_metadata.py.

Uses an in-memory SQLite database and injects fake TMDB client functions
so no live TMDB calls are made.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from commonplace_db.db import migrate
from commonplace_worker.handlers.video_metadata import (
    VideoDriveNotMounted,
    handle_ingest_movie,
    handle_ingest_tv,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with all migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def movie_dir(tmp_path: Path) -> Path:
    """A fake movie directory."""
    d = tmp_path / "Toy Story (1995) MULTi VFF 2160p BluRay x265-QTZ"
    d.mkdir()
    (d / "toy.story.mkv").write_bytes(b"fake video")
    return d


@pytest.fixture
def tv_dir(tmp_path: Path) -> Path:
    """A fake TV show directory."""
    d = tmp_path / "Andor (2022) Season 2 S02 (2160p HDR DSNP WEB-DL x265 HEVC 10bit)"
    d.mkdir()
    (d / "andor.s02e01.mkv").write_bytes(b"fake video")
    return d


def _fake_movie_search(title: str, year: int | None) -> dict[str, Any] | None:
    """Fake TMDB movie search returning a canned result."""
    return {
        "id": 862,
        "title": "Toy Story",
        "release_date": "1995-11-22",
        "overview": "A cowboy doll is threatened by a new spaceman figure.",
    }


def _fake_movie_details(tmdb_id: int) -> dict[str, Any] | None:
    """Fake TMDB movie details."""
    return {
        "id": 862,
        "title": "Toy Story",
        "overview": "A cowboy doll is threatened by a new spaceman figure.",
        "release_date": "1995-11-22",
        "genres": [{"id": 16, "name": "Animation"}, {"id": 35, "name": "Comedy"}],
        "director": "John Lasseter",
    }


def _fake_tv_search(title: str, year: int | None) -> dict[str, Any] | None:
    """Fake TMDB TV search returning a canned result."""
    return {
        "id": 83867,
        "name": "Andor",
        "first_air_date": "2022-09-21",
        "overview": "In an era filled with danger, deception...",
    }


def _fake_tv_details(tmdb_id: int) -> dict[str, Any] | None:
    """Fake TMDB TV details."""
    return {
        "id": 83867,
        "name": "Andor",
        "overview": "In an era filled with danger, deception...",
        "first_air_date": "2022-09-21",
        "genres": [{"id": 10759, "name": "Action & Adventure"}, {"id": 18, "name": "Drama"}],
        "number_of_seasons": 2,
    }


def _no_tmdb_search(title: str, year: int | None) -> dict[str, Any] | None:
    """TMDB search that returns nothing (no match)."""
    return None


def _no_tmdb_details(tmdb_id: int) -> dict[str, Any] | None:
    """TMDB details that returns nothing."""
    return None


# ---------------------------------------------------------------------------
# Movie handler: happy path
# ---------------------------------------------------------------------------


def test_ingest_movie_inserts_document(db_conn: sqlite3.Connection, movie_dir: Path) -> None:
    with patch("commonplace_worker.handlers.video_metadata._embed_plot"):
        result = handle_ingest_movie(
            {"path": str(movie_dir)},
            db_conn,
            _tmdb_search=_fake_movie_search,
            _tmdb_details=_fake_movie_details,
        )

    assert result["action"] == "inserted"
    assert result["document_id"] > 0

    row = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert row is not None
    assert row["content_type"] == "movie"
    assert row["title"] == "Toy Story"
    assert row["release_year"] == 1995
    assert row["media_type"] == "movie"
    assert row["plot"] is not None
    assert row["director"] == "John Lasseter"
    genres = json.loads(row["genres"])
    assert "Animation" in genres
    assert row["tmdb_id"] == 862
    assert row["filesystem_path"] == str(movie_dir)


def test_ingest_movie_idempotent_skip(db_conn: sqlite3.Connection, movie_dir: Path) -> None:
    """Second call with same path and existing plot should be skipped."""
    with patch("commonplace_worker.handlers.video_metadata._embed_plot"):
        result1 = handle_ingest_movie(
            {"path": str(movie_dir)},
            db_conn,
            _tmdb_search=_fake_movie_search,
            _tmdb_details=_fake_movie_details,
        )
    assert result1["action"] == "inserted"

    result2 = handle_ingest_movie(
        {"path": str(movie_dir)},
        db_conn,
        _tmdb_search=_fake_movie_search,
        _tmdb_details=_fake_movie_details,
    )
    assert result2["action"] == "skipped"
    assert result2["document_id"] == result1["document_id"]


def test_ingest_movie_no_tmdb_result(db_conn: sqlite3.Connection, movie_dir: Path) -> None:
    """When TMDB returns nothing, document is still inserted with parsed metadata."""
    with patch("commonplace_worker.handlers.video_metadata._embed_plot"):
        result = handle_ingest_movie(
            {"path": str(movie_dir)},
            db_conn,
            _tmdb_search=_no_tmdb_search,
            _tmdb_details=_no_tmdb_details,
        )

    assert result["action"] == "inserted"
    row = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert row["title"] == "Toy Story"
    assert row["plot"] is None  # no TMDB enrichment
    assert row["tmdb_id"] is None


def test_ingest_movie_embeds_plot_when_present(
    db_conn: sqlite3.Connection, movie_dir: Path
) -> None:
    """embed_plot should be called when plot is available."""
    embed_calls: list[tuple] = []

    def _fake_embed(conn: sqlite3.Connection, doc_id: int, plot: str, title: str) -> None:
        embed_calls.append((doc_id, plot, title))

    with patch(
        "commonplace_worker.handlers.video_metadata._embed_plot", side_effect=_fake_embed
    ):
        result = handle_ingest_movie(
            {"path": str(movie_dir)},
            db_conn,
            _tmdb_search=_fake_movie_search,
            _tmdb_details=_fake_movie_details,
        )

    assert len(embed_calls) == 1
    assert embed_calls[0][0] == result["document_id"]


def test_ingest_movie_missing_path_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="missing 'path'"):
        handle_ingest_movie({}, db_conn)


def test_ingest_movie_nonexistent_path_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FileNotFoundError):
        handle_ingest_movie(
            {"path": "/tmp/nonexistent_movie_dir_xyz"},
            db_conn,
        )


# ---------------------------------------------------------------------------
# TV handler: happy path
# ---------------------------------------------------------------------------


def test_ingest_tv_inserts_document(db_conn: sqlite3.Connection, tv_dir: Path) -> None:
    with patch("commonplace_worker.handlers.video_metadata._embed_plot"):
        result = handle_ingest_tv(
            {"path": str(tv_dir)},
            db_conn,
            _tmdb_search=_fake_tv_search,
            _tmdb_details=_fake_tv_details,
        )

    assert result["action"] == "inserted"
    assert result["document_id"] > 0

    row = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert row["content_type"] == "tv_show"
    assert row["title"] == "Andor"
    assert row["media_type"] == "tv_show"
    assert row["release_year"] == 2022
    assert row["season_count"] == 2
    assert row["plot"] is not None
    assert row["director"] is None  # TV shows don't have director
    assert row["tmdb_id"] == 83867
    genres = json.loads(row["genres"])
    assert "Drama" in genres


def test_ingest_tv_idempotent_skip(db_conn: sqlite3.Connection, tv_dir: Path) -> None:
    """Second call with same path and existing plot should be skipped."""
    with patch("commonplace_worker.handlers.video_metadata._embed_plot"):
        result1 = handle_ingest_tv(
            {"path": str(tv_dir)},
            db_conn,
            _tmdb_search=_fake_tv_search,
            _tmdb_details=_fake_tv_details,
        )
    result2 = handle_ingest_tv(
        {"path": str(tv_dir)},
        db_conn,
        _tmdb_search=_fake_tv_search,
        _tmdb_details=_fake_tv_details,
    )
    assert result2["action"] == "skipped"
    assert result2["document_id"] == result1["document_id"]


def test_ingest_tv_no_tmdb_result(db_conn: sqlite3.Connection, tv_dir: Path) -> None:
    """When TMDB returns nothing, document still inserted with fallback data."""
    with patch("commonplace_worker.handlers.video_metadata._embed_plot"):
        result = handle_ingest_tv(
            {"path": str(tv_dir)},
            db_conn,
            _tmdb_search=_no_tmdb_search,
            _tmdb_details=_no_tmdb_details,
        )

    assert result["action"] == "inserted"
    row = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert row["content_type"] == "tv_show"
    assert row["plot"] is None


def test_ingest_tv_missing_path_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="missing 'path'"):
        handle_ingest_tv({}, db_conn)


# ---------------------------------------------------------------------------
# Update path: row exists without plot
# ---------------------------------------------------------------------------


def test_ingest_movie_updates_when_no_plot(db_conn: sqlite3.Connection, movie_dir: Path) -> None:
    """If a row exists for the filesystem_path but plot is NULL, it should be updated."""
    import hashlib

    content_hash = hashlib.sha256(str(movie_dir).encode()).hexdigest()
    with db_conn:
        cur = db_conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, content_hash, status, filesystem_path, media_type)
            VALUES ('movie', ?, 'Placeholder', ?, 'pending', ?, 'movie')
            """,
            (str(movie_dir), content_hash, str(movie_dir)),
        )
    existing_id = cur.lastrowid

    with patch("commonplace_worker.handlers.video_metadata._embed_plot"):
        result = handle_ingest_movie(
            {"path": str(movie_dir)},
            db_conn,
            _tmdb_search=_fake_movie_search,
            _tmdb_details=_fake_movie_details,
        )

    assert result["action"] == "updated"
    assert result["document_id"] == existing_id

    row = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (existing_id,)
    ).fetchone()
    assert row["title"] == "Toy Story"
    assert row["plot"] is not None


# ---------------------------------------------------------------------------
# Drive mount check
# ---------------------------------------------------------------------------


def test_drive_not_mounted_raises(db_conn: sqlite3.Connection) -> None:
    fake_path = "/Volumes/Expansion/Movies/SomeMovie"
    with patch("pathlib.Path.exists", return_value=False), pytest.raises(VideoDriveNotMounted):
        handle_ingest_movie({"path": fake_path}, db_conn)


# ---------------------------------------------------------------------------
# Fallback embed text (TMDB returned no plot)
# ---------------------------------------------------------------------------


def _fake_movie_details_no_plot(tmdb_id: int) -> dict[str, Any] | None:
    """Fake TMDB movie details with no overview — compilation disc pattern."""
    return {
        "id": 862,
        "title": "Studio Ghibli Movie Collection",
        "overview": None,  # the gap we fall back on
        "release_date": "1985-03-02",
        "genres": [{"id": 16, "name": "Animation"}, {"id": 10751, "name": "Family"}],
        "director": "Hayao Miyazaki",
    }


def _fake_tv_details_no_plot(tmdb_id: int) -> dict[str, Any] | None:
    """Fake TMDB TV details with no overview."""
    return {
        "id": 83867,
        "name": "Obscure Show",
        "overview": "",
        "first_air_date": "2022-09-21",
        "genres": [{"id": 10759, "name": "Action & Adventure"}],
        "number_of_seasons": 1,
    }


class TestFallbackEmbedText:
    """Regression for post-Wave-5b observation: 11 docs landed with
    ``plot IS NULL`` and no chunks because TMDB returned no overview
    (compilation discs, obscure titles). Without a fallback the docs were
    invisible to semantic search. Fallback composes an embeddable string
    from title + media_type + genres + director + release_year so the
    judge has something to ground on."""

    def test_movie_no_plot_embeds_fallback_text(
        self, db_conn: sqlite3.Connection, movie_dir: Path
    ) -> None:
        """TMDB returns genres + director but no overview → handler should
        build a fallback embed string and pass it through to _embed_plot."""
        captured_text: list[str] = []

        def _capture_embed(
            _conn: sqlite3.Connection, _doc_id: int, text: str, _title: str
        ) -> None:
            captured_text.append(text)

        with patch(
            "commonplace_worker.handlers.video_metadata._embed_plot",
            side_effect=_capture_embed,
        ):
            result = handle_ingest_movie(
                {"path": str(movie_dir)},
                db_conn,
                _tmdb_search=_fake_movie_search,
                _tmdb_details=_fake_movie_details_no_plot,
            )

        assert result["action"] == "inserted"
        assert len(captured_text) == 1
        text = captured_text[0]
        # TMDB title wins over parsed filename title (see _build_doc_fields)
        assert "Studio Ghibli Movie Collection" in text
        assert "film" in text
        # Parsed-filename year wins when present (1995 from "Toy Story (1995)")
        assert "1995" in text
        assert "Hayao Miyazaki" in text
        assert "Animation" in text
        assert "Family" in text

    def test_tv_no_plot_embeds_fallback_with_season_count(
        self, db_conn: sqlite3.Connection, tv_dir: Path
    ) -> None:
        """TV fallback should include season count suffix."""
        captured_text: list[str] = []

        def _capture_embed(
            _conn: sqlite3.Connection, _doc_id: int, text: str, _title: str
        ) -> None:
            captured_text.append(text)

        with patch(
            "commonplace_worker.handlers.video_metadata._embed_plot",
            side_effect=_capture_embed,
        ):
            handle_ingest_tv(
                {"path": str(tv_dir)},
                db_conn,
                _tmdb_search=_fake_tv_search,
                _tmdb_details=_fake_tv_details_no_plot,
            )

        assert len(captured_text) == 1
        text = captured_text[0]
        assert "TV show" in text
        assert "1 season" in text
        assert "Action & Adventure" in text

    def test_plot_present_does_not_use_fallback(
        self, db_conn: sqlite3.Connection, movie_dir: Path
    ) -> None:
        """Sanity: when TMDB gives a real plot, the fallback is NOT used —
        the real plot text reaches the embedder."""
        captured_text: list[str] = []

        def _capture_embed(
            _conn: sqlite3.Connection, _doc_id: int, text: str, _title: str
        ) -> None:
            captured_text.append(text)

        with patch(
            "commonplace_worker.handlers.video_metadata._embed_plot",
            side_effect=_capture_embed,
        ):
            handle_ingest_movie(
                {"path": str(movie_dir)},
                db_conn,
                _tmdb_search=_fake_movie_search,
                _tmdb_details=_fake_movie_details,
            )

        assert len(captured_text) == 1
        assert captured_text[0].startswith("A cowboy doll")


class TestComposeFallbackEmbedText:
    """Unit tests on the composer itself — cover edge cases that are painful
    to stage via the full handler path."""

    def test_minimal_fields_still_produces_usable_text(self) -> None:
        from commonplace_worker.handlers.video_metadata import (
            _compose_fallback_embed_text,
        )

        text = _compose_fallback_embed_text(
            {"title": "Some Movie"}, is_tv=False
        )
        assert text == "Some Movie — film."

    def test_untitled_placeholder(self) -> None:
        from commonplace_worker.handlers.video_metadata import (
            _compose_fallback_embed_text,
        )

        text = _compose_fallback_embed_text({}, is_tv=True)
        assert "(untitled)" in text
        assert "TV show" in text

    def test_malformed_genres_json_skipped_silently(self) -> None:
        """Genres stored as a JSON array string — if ever malformed we should
        skip the genres segment, not raise."""
        from commonplace_worker.handlers.video_metadata import (
            _compose_fallback_embed_text,
        )

        text = _compose_fallback_embed_text(
            {"title": "Test", "genres": "not valid json"}, is_tv=False
        )
        assert "Genres" not in text
        assert text.startswith("Test")

    def test_season_pluralization(self) -> None:
        from commonplace_worker.handlers.video_metadata import (
            _compose_fallback_embed_text,
        )

        one = _compose_fallback_embed_text(
            {"title": "S", "season_count": 1}, is_tv=True
        )
        many = _compose_fallback_embed_text(
            {"title": "S", "season_count": 5}, is_tv=True
        )
        assert "1 season." in one
        assert "5 seasons." in many

