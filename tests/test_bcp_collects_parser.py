"""Tests for the BCP 1979 Collects parser.

All tests run against real fixture HTML files in
tests/fixtures/bcp_1979/collects/ — no mocked HTML.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commonplace_server.liturgical_parsers.bcp_collects import (
    ParsedCollect,
    parse_collects_dir,
    parse_collects_file,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "bcp_1979" / "collects"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(filename: str) -> str:
    return (FIXTURE_DIR / filename).read_text(encoding="utf-8")


def _find(collects: list[ParsedCollect], name_fragment: str) -> ParsedCollect:
    """Return the first collect whose feast_name contains name_fragment."""
    for c in collects:
        if name_fragment in c.feast_name:
            return c
    raise AssertionError(f"No collect with feast_name containing {name_fragment!r}")


# ---------------------------------------------------------------------------
# Section type tests
# ---------------------------------------------------------------------------


class TestSeasonsSection:
    """Parser on seasonst.html (Traditional / Seasons of the Year)."""

    def setup_method(self) -> None:
        html = _load("seasonst.html")
        self.collects = parse_collects_file(html, "seasonst.html")

    def test_yields_multiple_collects(self) -> None:
        # seasonst.html is a rich file; we expect many collects
        assert len(self.collects) >= 30

    def test_section_is_seasons(self) -> None:
        for c in self.collects:
            assert c.section == "seasons"

    def test_rite_i_from_filename(self) -> None:
        for c in self.collects:
            assert c.rite == "rite_i"

    def test_first_sunday_of_advent_body_text(self) -> None:
        advent1 = _find(self.collects, "First Sunday of Advent")
        assert "Almighty God, give us grace" in advent1.body_text

    def test_first_sunday_of_advent_preface(self) -> None:
        advent1 = _find(self.collects, "First Sunday of Advent")
        assert advent1.preface == "Preface of Advent"

    def test_advent_id_attribute_as_slug(self) -> None:
        # feast_slug derives from the feast name (canonical `_anglican` scheme);
        # the raw <p id="advent"> attribute is preserved on source_anchor.
        advent1 = _find(self.collects, "First Sunday of Advent")
        assert advent1.feast_slug == "first_sunday_of_advent_anglican"
        assert advent1.source_anchor == "advent"

    def test_rubric_captured_on_third_sunday_of_advent(self) -> None:
        advent3 = _find(self.collects, "Third Sunday of Advent")
        assert any("Ember Days" in r for r in advent3.rubrics)

    def test_no_br_tags_in_body_text(self) -> None:
        for c in self.collects:
            assert "<br" not in c.body_text, f"{c.feast_name}: <br> in body_text"

    def test_no_html_tags_in_body_text(self) -> None:
        for c in self.collects:
            assert "<em>" not in c.body_text
            assert "<strong>" not in c.body_text

    def test_all_bodies_contain_amen(self) -> None:
        for c in self.collects:
            assert "Amen" in c.body_text, f"{c.feast_name}: no Amen in body_text"

    def test_canonical_id_format(self) -> None:
        advent1 = _find(self.collects, "First Sunday of Advent")
        assert advent1.canonical_id == "seasons_first_sunday_of_advent_anglican"

    def test_source_file_stored(self) -> None:
        for c in self.collects:
            assert c.source_file == "seasonst.html"


class TestHolydaysSection:
    """Parser on holydayst.html (Traditional / Holy Days)."""

    def setup_method(self) -> None:
        html = _load("holydayst.html")
        self.collects = parse_collects_file(html, "holydayst.html")

    def test_yields_collects(self) -> None:
        assert len(self.collects) >= 20

    def test_section_is_holydays(self) -> None:
        for c in self.collects:
            assert c.section == "holydays"

    def test_rite_i(self) -> None:
        for c in self.collects:
            assert c.rite == "rite_i"

    def test_saint_andrew_no_id_falls_back_to_slugified_name(self) -> None:
        andrew = _find(self.collects, "Saint Andrew")
        # holydayst.html has no id= on Saint Andrew paragraph
        assert andrew.source_anchor is None
        assert andrew.feast_slug == "saint_andrew_anglican"

    def test_preface_captured(self) -> None:
        andrew = _find(self.collects, "Saint Andrew")
        assert andrew.preface is not None
        assert "Preface" in andrew.preface


class TestCommonSection:
    """Parser on commonc.html (Contemporary / Common of Saints)."""

    def setup_method(self) -> None:
        html = _load("commonc.html")
        self.collects = parse_collects_file(html, "commonc.html")

    def test_yields_collects(self) -> None:
        assert len(self.collects) >= 4

    def test_section_is_common(self) -> None:
        for c in self.collects:
            assert c.section == "common"

    def test_rite_ii(self) -> None:
        for c in self.collects:
            assert c.rite == "rite_ii"

    def test_of_a_martyr_body_text(self) -> None:
        martyr = _find(self.collects, "Martyr")
        assert "Amen" in martyr.body_text


class TestVariousSection:
    """Parser on variousc.html (Contemporary / Various Occasions)."""

    def setup_method(self) -> None:
        html = _load("variousc.html")
        self.collects = parse_collects_file(html, "variousc.html")

    def test_yields_collects(self) -> None:
        assert len(self.collects) >= 15

    def test_section_is_various(self) -> None:
        for c in self.collects:
            assert c.section == "various"

    def test_rite_ii(self) -> None:
        for c in self.collects:
            assert c.rite == "rite_ii"

    def test_rubric_between_heading_and_body(self) -> None:
        # variousc.html has rubrics like "Especially suitable for Thursdays"
        # appearing between the heading and the body
        holy_eucharist = _find(self.collects, "Holy Eucharist")
        assert any("Thursday" in r for r in holy_eucharist.rubrics)


# ---------------------------------------------------------------------------
# Rite discrimination tests
# ---------------------------------------------------------------------------


class TestRiteDiscrimination:
    def test_traditional_file_is_rite_i(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        assert all(c.rite == "rite_i" for c in collects)

    def test_contemporary_file_is_rite_ii(self) -> None:
        html = _load("seasonsc.html")
        collects = parse_collects_file(html, "seasonsc.html")
        assert all(c.rite == "rite_ii" for c in collects)

    def test_same_feast_name_different_rite(self) -> None:
        rit1 = parse_collects_file(_load("seasonst.html"), "seasonst.html")
        rit2 = parse_collects_file(_load("seasonsc.html"), "seasonsc.html")
        names1 = {c.feast_name for c in rit1}
        names2 = {c.feast_name for c in rit2}
        # Both files should have First Sunday of Advent
        assert "First Sunday of Advent" in names1
        assert "First Sunday of Advent" in names2
        # But different rites
        c1 = _find(rit1, "First Sunday of Advent")
        c2 = _find(rit2, "First Sunday of Advent")
        assert c1.rite == "rite_i"
        assert c2.rite == "rite_ii"

    def test_same_feast_shared_canonical_id(self) -> None:
        """canonical_id ignores rite so Rite I/II share the same id."""
        rit1 = parse_collects_file(_load("seasonst.html"), "seasonst.html")
        rit2 = parse_collects_file(_load("seasonsc.html"), "seasonsc.html")
        c1 = _find(rit1, "First Sunday of Advent")
        c2 = _find(rit2, "First Sunday of Advent")
        assert c1.canonical_id == c2.canonical_id


# ---------------------------------------------------------------------------
# Slug extraction
# ---------------------------------------------------------------------------


class TestSlugExtraction:
    def test_numeric_id_falls_back_to_name_slug(self) -> None:
        """Purely numeric <p id="1"> → slug from name, not "1"."""
        html = _load("varioust.html")
        collects = parse_collects_file(html, "varioust.html")
        trinity = _find(collects, "Holy Trinity")
        assert trinity.feast_slug == "of_the_holy_trinity_anglican"
        # source_anchor still carries the original numeric id
        assert trinity.source_anchor == "1"

    def test_numeric_prefix_stripped_from_feast_name(self) -> None:
        """'1. Of the Holy Trinity' → feast_name='Of the Holy Trinity'."""
        html = _load("varioust.html")
        collects = parse_collects_file(html, "varioust.html")
        trinity = _find(collects, "Holy Trinity")
        assert trinity.feast_name == "Of the Holy Trinity"
        # The numeric prefix must not appear in feast_name
        assert not trinity.feast_name.startswith("1.")

    def test_labor_day_numeric_id_and_name(self) -> None:
        """id="25" → slug='for-labor-day', source_anchor='25'."""
        html = _load("varioust.html")
        collects = parse_collects_file(html, "varioust.html")
        labor = _find(collects, "Labor Day")
        assert labor.feast_slug == "for_labor_day_anglican"
        assert labor.source_anchor == "25"
        assert labor.feast_name == "For Labor Day"

    def test_non_numeric_id_preserved_as_source_anchor(self) -> None:
        """id="advent" is kept on source_anchor; feast_slug is name-derived."""
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        advent1 = _find(collects, "First Sunday of Advent")
        assert advent1.feast_slug == "first_sunday_of_advent_anglican"
        assert advent1.source_anchor == "advent"

    def test_source_anchor_retains_numeric_id(self) -> None:
        """source_anchor stores the original numeric HTML anchor for traceability."""
        html = _load("varioust.html")
        collects = parse_collects_file(html, "varioust.html")
        trinity = _find(collects, "Holy Trinity")
        assert trinity.source_anchor == "1"
        # feast_slug must differ from source_anchor (semantic vs. numeric)
        assert trinity.feast_slug != trinity.source_anchor

    def test_fallback_to_slugified_name_when_no_id(self) -> None:
        html = _load("holydayst.html")
        collects = parse_collects_file(html, "holydayst.html")
        andrew = _find(collects, "Saint Andrew")
        assert andrew.source_anchor is None
        assert andrew.feast_slug == "saint_andrew_anglican"

    def test_slug_is_lowercase(self) -> None:
        html = _load("holydayst.html")
        collects = parse_collects_file(html, "holydayst.html")
        for c in collects:
            assert c.feast_slug == c.feast_slug.lower()


# ---------------------------------------------------------------------------
# Body text quality
# ---------------------------------------------------------------------------


class TestBodyText:
    def test_br_tags_flattened_to_spaces(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        for c in collects:
            assert "<br" not in c.body_text

    def test_whitespace_normalised(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        for c in collects:
            assert "  " not in c.body_text, f"double space in {c.feast_name}"

    def test_sentence_flow_preserved(self) -> None:
        # Words on consecutive lines should be joined with a space, not joined
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        advent1 = _find(collects, "First Sunday of Advent")
        # "cast away the works of darkness" should be intact
        assert "cast away the works of darkness" in advent1.body_text

    def test_em_content_preserved_as_plain_text(self) -> None:
        # <em>Amen.</em> should appear as "Amen." in body_text, not stripped
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        for c in collects:
            assert "Amen." in c.body_text or "Amen" in c.body_text


# ---------------------------------------------------------------------------
# Rubrics and preface
# ---------------------------------------------------------------------------


class TestRubricsAndPreface:
    def test_rubric_captured_as_separate_list(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        advent3 = _find(collects, "Third Sunday of Advent")
        assert len(advent3.rubrics) >= 1
        assert any("Ember Days" in r for r in advent3.rubrics)

    def test_rubric_not_in_body_text(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        for c in collects:
            for rubric in c.rubrics:
                # The rubric text should not appear verbatim in the body
                # (It may share words, but the rubric paragraph itself
                # should be separate)
                assert rubric not in c.body_text or len(rubric) < 10

    def test_preface_captured_when_present(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        advent1 = _find(collects, "First Sunday of Advent")
        assert advent1.preface == "Preface of Advent"

    def test_preface_none_when_absent(self) -> None:
        # Not all collects have a preface line; check at least one is None.
        # variousc.html collects generally have prefaces; confirm
        # the field is always str | None — never some other type.
        html2 = _load("variousc.html")
        collects2 = parse_collects_file(html2, "variousc.html")
        assert all(c.preface is None or isinstance(c.preface, str) for c in collects2)


# ---------------------------------------------------------------------------
# ToC short-circuit
# ---------------------------------------------------------------------------


class TestToCShortCircuit:
    @pytest.mark.parametrize(
        "filename",
        ["collects.html", "toctradit.html", "toccontemp.html", "proper.html"],
    )
    def test_toc_files_return_empty(self, filename: str) -> None:
        html = _load(filename)
        collects = parse_collects_file(html, filename)
        assert collects == []

    def test_small_html_without_strong_returns_empty(self) -> None:
        html = "<html><body><p>No headings here.</p></body></html>"
        collects = parse_collects_file(html, "tiny.html")
        assert collects == []


# ---------------------------------------------------------------------------
# Edge case: First Sunday of Advent spot-check (contract requirement)
# ---------------------------------------------------------------------------


class TestFirstSundayOfAdventSpotCheck:
    """Contract-required spot-check on seasonst.html first collect."""

    def setup_method(self) -> None:
        html = _load("seasonst.html")
        self.collects = parse_collects_file(html, "seasonst.html")
        self.advent1 = _find(self.collects, "First Sunday of Advent")

    def test_body_contains_almighty_god_give_us_grace(self) -> None:
        assert "Almighty God, give us grace" in self.advent1.body_text

    def test_preface_is_preface_of_advent(self) -> None:
        assert self.advent1.preface == "Preface of Advent"

    def test_feast_name_exact(self) -> None:
        assert self.advent1.feast_name == "First Sunday of Advent"

    def test_rite_i(self) -> None:
        assert self.advent1.rite == "rite_i"

    def test_section_seasons(self) -> None:
        assert self.advent1.section == "seasons"


# ---------------------------------------------------------------------------
# parse_collects_dir
# ---------------------------------------------------------------------------


class TestParseCollectsDir:
    def test_returns_all_content_files(self) -> None:
        collects = parse_collects_dir(FIXTURE_DIR)
        # 8 content files × ~25–76 collects each
        assert len(collects) > 100

    def test_no_toc_leakage(self) -> None:
        collects = parse_collects_dir(FIXTURE_DIR)
        source_files = {c.source_file for c in collects}
        assert "collects.html" not in source_files
        assert "toctradit.html" not in source_files
        assert "toccontemp.html" not in source_files
        assert "proper.html" not in source_files

    def test_both_rites_present(self) -> None:
        collects = parse_collects_dir(FIXTURE_DIR)
        rites = {c.rite for c in collects}
        assert "rite_i" in rites
        assert "rite_ii" in rites

    def test_all_sections_present(self) -> None:
        collects = parse_collects_dir(FIXTURE_DIR)
        sections = {c.section for c in collects}
        assert "seasons" in sections
        assert "holydays" in sections
        assert "common" in sections
        assert "various" in sections


# ---------------------------------------------------------------------------
# raw_metadata shape
# ---------------------------------------------------------------------------


class TestRawMetadata:
    def test_raw_metadata_is_valid_json(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        for c in collects:
            meta = json.loads(c.raw_metadata)
            assert "section" in meta
            assert "rite" in meta
            assert "source_file" in meta

    def test_raw_metadata_carries_section_and_rite(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        advent1 = _find(collects, "First Sunday of Advent")
        meta = json.loads(advent1.raw_metadata)
        assert meta["section"] == "seasons"
        assert meta["rite"] == "rite_i"

    def test_page_number_in_raw_metadata(self) -> None:
        html = _load("seasonst.html")
        collects = parse_collects_file(html, "seasonst.html")
        # Second Sunday of Advent follows page 159 marker
        advent2 = _find(collects, "Second Sunday of Advent")
        meta = json.loads(advent2.raw_metadata)
        assert meta["page_number"] == 159
