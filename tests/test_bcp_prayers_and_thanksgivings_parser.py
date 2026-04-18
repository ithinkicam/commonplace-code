"""Tests for the BCP 1979 Prayers and Thanksgivings parser.

All tests run against real fixture HTML files in
tests/fixtures/bcp_1979/prayers_and_thanksgivings/ — no mocked HTML.

The fixture files are verbatim copies of the cached bcponline.org pages:
  - Prayers.html      (70 numbered prayers)
  - Thanksgivings.html (11 numbered thanksgivings)
"""

from __future__ import annotations

import json
from pathlib import Path

from commonplace_server.liturgical_parsers.bcp_prayers_and_thanksgivings import (
    ParsedPrayer,
    parse_prayers_and_thanksgivings,
    parse_prayers_file,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "bcp_1979" / "prayers_and_thanksgivings"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_prayers() -> list[ParsedPrayer]:
    html = (FIXTURE_DIR / "Prayers.html").read_text(encoding="utf-8")
    return parse_prayers_file(html, "Prayers.html", genre="prayer")


def _load_thanksgivings() -> list[ParsedPrayer]:
    html = (FIXTURE_DIR / "Thanksgivings.html").read_text(encoding="utf-8")
    return parse_prayers_file(html, "Thanksgivings.html", genre="thanksgiving")


def _find(records: list[ParsedPrayer], number: int) -> ParsedPrayer:
    for r in records:
        if r.prayer_number == number:
            return r
    raise AssertionError(f"No prayer with prayer_number={number}")


def _find_by_title(records: list[ParsedPrayer], fragment: str) -> ParsedPrayer:
    for r in records:
        if fragment in r.title:
            return r
    raise AssertionError(f"No prayer with title containing {fragment!r}")


# ---------------------------------------------------------------------------
# Overall counts
# ---------------------------------------------------------------------------


class TestCounts:
    """Verify expected count of prayers and thanksgivings."""

    def test_prayers_count_is_70(self) -> None:
        prayers = _load_prayers()
        assert len(prayers) == 70

    def test_thanksgivings_count_is_11(self) -> None:
        thanks = _load_thanksgivings()
        assert len(thanks) == 11

    def test_combined_count_is_81(self) -> None:
        all_records = parse_prayers_and_thanksgivings(
            FIXTURE_DIR / "Prayers.html",
            FIXTURE_DIR / "Thanksgivings.html",
        )
        assert len(all_records) == 81

    def test_prayer_numbers_1_through_70(self) -> None:
        prayers = _load_prayers()
        nums = {p.prayer_number for p in prayers}
        assert nums == set(range(1, 71))

    def test_thanksgiving_numbers_1_through_11(self) -> None:
        thanks = _load_thanksgivings()
        nums = {t.prayer_number for t in thanks}
        assert nums == set(range(1, 12))


# ---------------------------------------------------------------------------
# Section headers — sample across major sections
# ---------------------------------------------------------------------------


class TestSectionHeaders:
    """Prayers are grouped under the correct section headers."""

    def setup_method(self) -> None:
        self.prayers = _load_prayers()
        self.thanks = _load_thanksgivings()

    # Prayers sections
    def test_prayers_for_the_world(self) -> None:
        for num in range(1, 7):
            p = _find(self.prayers, num)
            assert p.section_header == "Prayers for the World", (
                f"Prayer {num} has section {p.section_header!r}"
            )

    def test_prayers_for_the_church(self) -> None:
        for num in range(7, 18):
            p = _find(self.prayers, num)
            assert p.section_header == "Prayers for the Church", (
                f"Prayer {num} has section {p.section_header!r}"
            )

    def test_prayers_for_national_life(self) -> None:
        for num in range(18, 27):
            p = _find(self.prayers, num)
            assert p.section_header == "Prayers for National Life", (
                f"Prayer {num} has section {p.section_header!r}"
            )

    def test_prayers_for_social_order(self) -> None:
        for num in range(27, 40):
            p = _find(self.prayers, num)
            assert p.section_header == "Prayers for the Social Order", (
                f"Prayer {num} has section {p.section_header!r}"
            )

    def test_prayers_for_natural_order(self) -> None:
        for num in range(40, 45):
            p = _find(self.prayers, num)
            assert p.section_header == "Prayers for the Natural Order", (
                f"Prayer {num} has section {p.section_header!r}"
            )

    def test_prayers_for_family_and_personal_life(self) -> None:
        for num in range(45, 63):
            p = _find(self.prayers, num)
            assert p.section_header == "Prayers for Family and Personal Life", (
                f"Prayer {num} has section {p.section_header!r}"
            )

    def test_other_prayers(self) -> None:
        for num in range(63, 71):
            p = _find(self.prayers, num)
            assert p.section_header == "Other Prayers", (
                f"Prayer {num} has section {p.section_header!r}"
            )

    # Thanksgivings sections
    def test_thanksgiving_general_section(self) -> None:
        for num in (1, 2):
            t = _find(self.thanks, num)
            assert t.section_header == "General Thanksgivings", (
                f"Thanksgiving {num} has section {t.section_header!r}"
            )

    def test_thanksgiving_church_section(self) -> None:
        for num in (3, 4):
            t = _find(self.thanks, num)
            assert t.section_header == "Thanksgivings for the Church", (
                f"Thanksgiving {num} has section {t.section_header!r}"
            )

    def test_thanksgiving_national_life_section(self) -> None:
        for num in (5, 6):
            t = _find(self.thanks, num)
            assert t.section_header == "Thanksgivings for National Life", (
                f"Thanksgiving {num} has section {t.section_header!r}"
            )

    def test_thanksgiving_natural_order_section(self) -> None:
        for num in (8, 9):
            t = _find(self.thanks, num)
            assert t.section_header == "Thanksgivings for the Natural Order", (
                f"Thanksgiving {num} has section {t.section_header!r}"
            )

    def test_thanksgiving_family_section(self) -> None:
        for num in (10, 11):
            t = _find(self.thanks, num)
            assert t.section_header == "Thanksgivings for Family and Personal Life", (
                f"Thanksgiving {num} has section {t.section_header!r}"
            )


# ---------------------------------------------------------------------------
# Body text quality
# ---------------------------------------------------------------------------


class TestBodyTextQuality:
    def setup_method(self) -> None:
        self.prayers = _load_prayers()
        self.thanks = _load_thanksgivings()

    def test_all_prayers_have_amen(self) -> None:
        for p in self.prayers:
            assert "Amen" in p.body_text, f"Prayer {p.prayer_number} lacks Amen"

    def test_all_thanksgivings_have_amen(self) -> None:
        for t in self.thanks:
            assert "Amen" in t.body_text, f"Thanksgiving {t.prayer_number} lacks Amen"

    def test_no_html_tags_in_body(self) -> None:
        for p in self.prayers + self.thanks:
            assert "<" not in p.body_text, (
                f"{p.prayer_number}: HTML tag in body_text"
            )

    def test_no_double_spaces_in_body(self) -> None:
        for p in self.prayers + self.thanks:
            assert "  " not in p.body_text, (
                f"{p.prayer_number}: double space in body_text"
            )

    def test_prayer_1_body_starts_with_o_heavenly(self) -> None:
        p = _find(self.prayers, 1)
        assert p.body_text.startswith("O heavenly Father")

    def test_prayer_3_body_contains_compassion(self) -> None:
        p = _find(self.prayers, 3)
        assert "compassion" in p.body_text

    def test_prayer_18_body_contains_good_land(self) -> None:
        p = _find(self.prayers, 18)
        assert "good land" in p.body_text

    def test_prayer_27_body_contains_social_justice(self) -> None:
        p = _find(self.prayers, 27)
        assert "barriers" in p.body_text or "justice" in p.body_text

    def test_prayer_40_body_contains_universe(self) -> None:
        p = _find(self.prayers, 40)
        assert "universe" in p.body_text

    def test_prayer_45_body_contains_families(self) -> None:
        p = _find(self.prayers, 45)
        assert "homes" in p.body_text or "families" in p.body_text.lower()

    def test_prayer_62_st_francis(self) -> None:
        p = _find(self.prayers, 62)
        assert "instruments" in p.body_text or "peace" in p.body_text

    def test_prayer_63_in_the_evening_body(self) -> None:
        p = _find(self.prayers, 63)
        assert "shadows" in p.body_text or "evening" in p.body_text.lower()

    def test_thanksgiving_1_body_contains_accept(self) -> None:
        t = _find(self.thanks, 1)
        assert "Accept, O Lord" in t.body_text

    def test_thanksgiving_1_body_no_1979_version_prefix(self) -> None:
        """Malformed '(1979 Version)' prefix must be stripped from Thanksgiving 1."""
        t = _find(self.thanks, 1)
        assert not t.body_text.startswith("(1979 Version)")

    def test_thanksgiving_5_body_contains_nation(self) -> None:
        t = _find(self.thanks, 5)
        assert "nation" in t.body_text.lower()

    def test_thanksgiving_8_beauty_of_earth(self) -> None:
        t = _find(self.thanks, 8)
        assert "beauty" in t.body_text or "earth" in t.body_text.lower()

    def test_thanksgiving_11_restoration_of_health(self) -> None:
        t = _find(self.thanks, 11)
        assert "sickness" in t.body_text or "health" in t.body_text.lower()


# ---------------------------------------------------------------------------
# Slug and canonical_id
# ---------------------------------------------------------------------------


class TestSlugs:
    def setup_method(self) -> None:
        self.prayers = _load_prayers()
        self.thanks = _load_thanksgivings()

    def test_all_slugs_end_with_anglican(self) -> None:
        for p in self.prayers + self.thanks:
            assert p.slug.endswith("_anglican"), f"Slug {p.slug!r} lacks _anglican"

    def test_all_slugs_lowercase(self) -> None:
        for p in self.prayers + self.thanks:
            assert p.slug == p.slug.lower(), f"Slug not lowercase: {p.slug!r}"

    def test_prayer_1_slug(self) -> None:
        p = _find(self.prayers, 1)
        assert p.slug == "for_joy_in_god_s_creation_prayer_1_anglican"

    def test_prayer_62_st_francis_slug(self) -> None:
        p = _find(self.prayers, 62)
        assert p.slug == "a_prayer_attributed_to_st_francis_prayer_62_anglican"

    def test_thanksgiving_1_slug(self) -> None:
        t = _find(self.thanks, 1)
        assert t.slug == "a_general_thanksgiving_thanksgiving_1_anglican"

    def test_canonical_id_equals_slug(self) -> None:
        for p in self.prayers + self.thanks:
            assert p.canonical_id == p.slug


# ---------------------------------------------------------------------------
# Genre and metadata
# ---------------------------------------------------------------------------


class TestGenreAndMetadata:
    def setup_method(self) -> None:
        self.prayers = _load_prayers()
        self.thanks = _load_thanksgivings()

    def test_prayers_have_genre_prayer(self) -> None:
        for p in self.prayers:
            assert p.genre == "prayer", f"Prayer {p.prayer_number} genre={p.genre!r}"

    def test_thanksgivings_have_genre_thanksgiving(self) -> None:
        for t in self.thanks:
            assert t.genre == "thanksgiving", (
                f"Thanksgiving {t.prayer_number} genre={t.genre!r}"
            )

    def test_raw_metadata_is_valid_json(self) -> None:
        for p in self.prayers + self.thanks:
            meta = json.loads(p.raw_metadata)
            assert isinstance(meta, dict)

    def test_raw_metadata_required_keys(self) -> None:
        required = {
            "prayer_number",
            "section_header",
            "source_anchor",
            "source_file",
            "page_number",
            "genre",
            "category",
            "tradition",
            "source",
        }
        for p in self.prayers + self.thanks:
            meta = json.loads(p.raw_metadata)
            assert required.issubset(set(meta.keys())), (
                f"Prayer {p.prayer_number} missing metadata keys: "
                f"{required - set(meta.keys())}"
            )

    def test_category_is_devotional_manual(self) -> None:
        for p in self.prayers + self.thanks:
            meta = json.loads(p.raw_metadata)
            assert meta["category"] == "devotional_manual", (
                f"Prayer {p.prayer_number} category={meta['category']!r}"
            )

    def test_tradition_is_anglican(self) -> None:
        for p in self.prayers + self.thanks:
            meta = json.loads(p.raw_metadata)
            assert meta["tradition"] == "anglican"

    def test_source_is_bcp_1979(self) -> None:
        for p in self.prayers + self.thanks:
            meta = json.loads(p.raw_metadata)
            assert meta["source"] == "bcp_1979"

    def test_prayer_number_in_metadata_matches_field(self) -> None:
        for p in self.prayers + self.thanks:
            meta = json.loads(p.raw_metadata)
            assert meta["prayer_number"] == p.prayer_number

    def test_thanksgiving_number_in_metadata(self) -> None:
        for t in self.thanks:
            meta = json.loads(t.raw_metadata)
            assert "thanksgiving_number" in meta
            assert meta["thanksgiving_number"] == t.prayer_number

    def test_source_anchor_is_numeric_string(self) -> None:
        for p in self.prayers + self.thanks:
            if p.source_anchor is not None:
                assert p.source_anchor.isdigit(), (
                    f"Prayer {p.prayer_number} source_anchor={p.source_anchor!r}"
                )

    def test_page_number_in_range(self) -> None:
        for p in self.prayers + self.thanks:
            if p.page_number is not None:
                assert 810 <= p.page_number <= 841, (
                    f"Prayer {p.prayer_number} page_number={p.page_number} out of range"
                )

    def test_prayer_2_has_page_814(self) -> None:
        """Prayer 2 body spans the page 814 marker."""
        p = _find(self.prayers, 2)
        assert p.page_number == 814

    def test_section_header_in_metadata(self) -> None:
        for p in self.prayers + self.thanks:
            meta = json.loads(p.raw_metadata)
            assert meta["section_header"] == p.section_header


# ---------------------------------------------------------------------------
# Specific spot checks across sections
# ---------------------------------------------------------------------------


class TestSpotChecks:
    """Spot-check prayers from each major section and both files."""

    def setup_method(self) -> None:
        self.prayers = _load_prayers()
        self.thanks = _load_thanksgivings()

    def test_prayer_4_for_peace_title(self) -> None:
        p = _find(self.prayers, 4)
        assert "Peace" in p.title

    def test_prayer_7_for_the_church_body(self) -> None:
        p = _find(self.prayers, 7)
        assert "Catholic Church" in p.body_text

    def test_prayer_22_sound_government_litany_format(self) -> None:
        """Prayer 22 has a litany format; body should contain all parts."""
        p = _find(self.prayers, 22)
        assert "Lord, keep this nation under your care" in p.body_text
        assert "Give grace to your servants" in p.body_text

    def test_prayer_50_and_51_both_birthday_distinct_slugs(self) -> None:
        """Prayers 50 and 51 share title 'For a Birthday' but must have distinct slugs."""
        p50 = _find(self.prayers, 50)
        p51 = _find(self.prayers, 51)
        assert p50.title == "For a Birthday"
        assert p51.title == "For a Birthday"
        assert p50.slug == "for_a_birthday_prayer_50_anglican"
        assert p51.slug == "for_a_birthday_prayer_51_anglican"
        assert p50.slug != p51.slug

    def test_prayer_57_and_58_both_guidance_distinct_slugs(self) -> None:
        """Prayers 57 and 58 share title 'For Guidance' but must have distinct slugs."""
        p57 = _find(self.prayers, 57)
        p58 = _find(self.prayers, 58)
        assert p57.title == "For Guidance"
        assert p58.title == "For Guidance"
        assert p57.slug == "for_guidance_prayer_57_anglican"
        assert p58.slug == "for_guidance_prayer_58_anglican"
        assert p57.slug != p58.slug

    def test_prayer_70_grace_at_meals_multiple_forms(self) -> None:
        """Prayer 70 includes multiple forms ('or this')."""
        p = _find(self.prayers, 70)
        assert "or this" in p.body_text.lower() or "Bless, O Lord" in p.body_text

    def test_thanksgiving_2_litany_format(self) -> None:
        """Thanksgiving 2 is a litany with 'We thank you, Lord' responses."""
        t = _find(self.thanks, 2)
        assert "We thank you, Lord" in t.body_text

    def test_thanksgiving_3_mission_church(self) -> None:
        t = _find(self.thanks, 3)
        assert "reconcile" in t.body_text or "Jesus Christ" in t.body_text

    def test_thanksgiving_4_saints_faithful_departed(self) -> None:
        t = _find(self.thanks, 4)
        assert "Abraham" in t.body_text or "saints" in t.body_text.lower()

    def test_thanksgiving_7_diversity_races(self) -> None:
        t = _find(self.thanks, 7)
        assert "races" in t.body_text or "diversity" in t.body_text.lower()

    def test_source_file_correct_for_prayers(self) -> None:
        for p in self.prayers:
            assert p.source_file == "Prayers.html"

    def test_source_file_correct_for_thanksgivings(self) -> None:
        for t in self.thanks:
            assert t.source_file == "Thanksgivings.html"
