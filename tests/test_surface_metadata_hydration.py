"""Unit tests for metadata-assisted hydration in surface.py (Phase 4 Wave 4.14 path R).

These tests isolate ``_hydrate_title_matches`` and its helpers from the live
embedding / retrieval stack by seeding an in-memory SQLite DB with a minimal
documents + chunks + commemoration_bio fixture.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from commonplace_server.surface import (
    _derive_match_phrases,
    _hydrate_title_matches,
    _phrase_in_seed,
)

# --- _derive_match_phrases --------------------------------------------------


class TestDeriveMatchPhrases:
    def test_returns_empty_for_empty_title(self) -> None:
        assert _derive_match_phrases("") == []

    def test_returns_full_title_alone_when_no_prefix(self) -> None:
        assert _derive_match_phrases("Psalm 23") == ["Psalm 23"]

    def test_strips_collect_for_prefix(self) -> None:
        phrases = _derive_match_phrases("Collect for Julian of Norwich")
        assert "Collect for Julian of Norwich" in phrases
        assert "Julian of Norwich" in phrases

    def test_strips_an_order_for_prefix(self) -> None:
        phrases = _derive_match_phrases("An Order for Compline")
        assert "An Order for Compline" in phrases
        assert "Compline" in phrases

    def test_extracts_parenthesized_content(self) -> None:
        phrases = _derive_match_phrases("Optional Block (Ash Wednesday)")
        assert "Optional Block (Ash Wednesday)" in phrases
        assert "Ash Wednesday" in phrases

    def test_rejects_short_derived_phrase(self) -> None:
        """'A Collect for Peace' → 'Peace' (5 chars) must NOT be a match phrase,
        since a word like 'peace' would false-positive on any seed using it."""
        phrases = _derive_match_phrases("A Collect for Peace")
        assert "A Collect for Peace" in phrases
        assert "Peace" not in phrases

    def test_accepts_derived_phrase_at_min_length(self) -> None:
        """'An Order for Compline' → 'Compline' (8 chars) is specific enough."""
        phrases = _derive_match_phrases("An Order for Compline")
        assert "Compline" in phrases

    def test_dedupes_case_insensitively(self) -> None:
        phrases = _derive_match_phrases("JULIAN OF NORWICH")
        assert len(phrases) == len({p.lower() for p in phrases})


# --- _phrase_in_seed --------------------------------------------------------


class TestPhraseInSeed:
    def test_word_boundary_match(self) -> None:
        assert _phrase_in_seed("Compline", "compline anglican book of common prayer")

    def test_case_insensitive(self) -> None:
        assert _phrase_in_seed("COMPLINE", "compline is the night office")

    def test_requires_word_boundary_no_partial_match(self) -> None:
        # 'peace' should not match inside 'peaceful'.
        assert not _phrase_in_seed("peace", "a peaceful resolution")

    def test_multi_word_phrase(self) -> None:
        assert _phrase_in_seed(
            "Julian of Norwich", "collect for julian of norwich anglican"
        )


# --- _hydrate_title_matches with in-memory DB -------------------------------


@pytest.fixture
def seeded_conn() -> Iterator[sqlite3.Connection]:
    """Minimal documents + chunks + commemoration_bio schema with a handful
    of fixture rows covering the canonical test cases."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            content_type TEXT,
            source_uri TEXT,
            title TEXT,
            source_id TEXT,
            created_at TEXT
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER,
            chunk_index INTEGER,
            text TEXT
        );
        CREATE TABLE commemoration_bio (
            id INTEGER PRIMARY KEY,
            document_id INTEGER
        );
        """
    )
    # Seed a representative mix:
    # 1 = LFF Julian collect (liturgical_unit)
    # 2 = LFF Julian bio (prose, joined via commemoration_bio)
    # 3 = BCP Compline (liturgical_unit)
    # 4 = BCP Psalm 23 (liturgical_unit)
    # 5 = BCP Collect for Peace (liturgical_unit — MUST NOT match generic peace-seed)
    # 6 = Book about grief (prose, NOT a bio — MUST NOT match even if title overlaps)
    rows = [
        (1, "liturgical_unit", "lff2024://collect/julian", "Collect for Julian of Norwich",
         "julian_of_norwich_rite-i", "2024-01-01"),
        (2, "prose", "lff2024://commemoration/julian", "Julian of Norwich",
         "julian_of_norwich", "2024-01-01"),
        (3, "liturgical_unit", "bcp://compline", "An Order for Compline",
         "an_order_for_compline_anglican", "2024-01-01"),
        (4, "liturgical_unit", "bcp://psalter/23", "Psalm 23",
         "psalm_023_anglican", "2024-01-01"),
        (5, "liturgical_unit", "bcp://collects/peace", "A Collect for Peace",
         "a_collect_for_peace_anglican", "2024-01-01"),
        (6, "prose", "book://grief", "Julian of Norwich: The Showings",
         "book_julian_showings", "2024-01-01"),
    ]
    conn.executemany(
        "INSERT INTO documents (id, content_type, source_uri, title, source_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    # One chunk per document.
    conn.executemany(
        "INSERT INTO chunks (id, document_id, chunk_index, text) VALUES (?, ?, 0, ?)",
        [(i, i, f"chunk text {i}") for i in range(1, 7)],
    )
    # Register doc 2 (Julian bio) as a commemoration.
    conn.execute("INSERT INTO commemoration_bio (id, document_id) VALUES (1, 2)")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


class TestHydrateTitleMatches:
    def test_hydrates_julian_collect_and_bio_for_named_seed(
        self, seeded_conn: sqlite3.Connection
    ) -> None:
        seed = "Collect for Julian of Norwich Anglican — love was his meaning."
        hits = _hydrate_title_matches(seeded_conn, seed)
        doc_ids = {h.document_id for h in hits}
        assert 1 in doc_ids, "should hydrate Julian's LFF collect"
        assert 2 in doc_ids, "should hydrate Julian's LFF bio via commemoration_bio"

    def test_hydrates_compline_for_office_named_seed(
        self, seeded_conn: sqlite3.Connection
    ) -> None:
        # Seed names "Compline" but not "An Order for Compline" — derived-phrase
        # stripping must reach the match.
        seed = "Compline Anglican Book of Common Prayer — saying the night office."
        hits = _hydrate_title_matches(seeded_conn, seed)
        assert 3 in {h.document_id for h in hits}

    def test_hydrates_psalm_23_with_verse_quote(
        self, seeded_conn: sqlite3.Connection
    ) -> None:
        seed = "Psalm 23 Dominus regit me — 'though I walk through the valley.'"
        hits = _hydrate_title_matches(seeded_conn, seed)
        assert 4 in {h.document_id for h in hits}

    def test_does_not_hydrate_collect_for_peace_on_generic_peace_seed(
        self, seeded_conn: sqlite3.Connection
    ) -> None:
        """Short derived phrase 'Peace' must not false-positive on a seed
        using the common word 'peace' — the ≥6-char derived-phrase rule."""
        seed = (
            "The ceasefire language is doing a lot of work — it's being called "
            "'peace' in the communiqués."
        )
        hits = _hydrate_title_matches(seeded_conn, seed)
        assert 5 not in {h.document_id for h in hits}

    def test_excludes_ordinary_prose_even_when_title_matches(
        self, seeded_conn: sqlite3.Connection
    ) -> None:
        """Book titled 'Julian of Norwich: The Showings' is prose but NOT
        a commemoration_bio, so it should not be hydrated."""
        seed = "Julian of Norwich was a medieval anchoress."
        hits = _hydrate_title_matches(seeded_conn, seed)
        # Doc 2 (bio, in commemoration_bio) is hydrated; doc 6 (book) is not.
        doc_ids = {h.document_id for h in hits}
        assert 2 in doc_ids
        assert 6 not in doc_ids

    def test_returns_empty_for_seed_with_no_canonical_names(
        self, seeded_conn: sqlite3.Connection
    ) -> None:
        seed = "Today I made pasta carbonara. The eggs almost scrambled."
        assert _hydrate_title_matches(seeded_conn, seed) == []

    def test_synthetic_score_is_zero(self, seeded_conn: sqlite3.Connection) -> None:
        seed = "Collect for Julian of Norwich is a meditation on love."
        hits = _hydrate_title_matches(seeded_conn, seed)
        assert hits, "expected at least one hit"
        assert all(h.score == 0.0 for h in hits)

    def test_prefers_longer_matches_and_honours_limit(
        self, seeded_conn: sqlite3.Connection
    ) -> None:
        # Seed matches both Julian docs. With limit=1, prefer the longer
        # phrase-matched doc (Julian collect, title "Collect for Julian of Norwich")
        # over the bio (title "Julian of Norwich").
        seed = "Collect for Julian of Norwich Anglican. Julian of Norwich."
        hits = _hydrate_title_matches(seeded_conn, seed, limit=1)
        assert len(hits) == 1
        assert hits[0].document_id == 1  # the collect, longer title
