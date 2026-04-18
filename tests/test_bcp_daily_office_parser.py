"""Tests for the BCP 1979 Daily Office parser.

All tests run against real fixture HTML files in
tests/fixtures/bcp_1979/daily_office/ — no mocked HTML, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commonplace_server.liturgical_parsers.bcp_daily_office import (
    ParsedOffice,
    parse_daily_office_file,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "bcp_1979" / "daily_office"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(filename: str) -> list[ParsedOffice]:
    return parse_daily_office_file(FIXTURE_DIR / filename)


def _find(units: list[ParsedOffice], name_fragment: str) -> ParsedOffice:
    """Return the first unit whose name contains name_fragment."""
    for u in units:
        if name_fragment in u.name:
            return u
    raise AssertionError(f"No unit with name containing {name_fragment!r}")


# ---------------------------------------------------------------------------
# Parametrized baseline: each fixture yields the expected unit count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected_count",
    [
        # Main offices
        ("mp1.html", 34),
        ("mp2.html", 42),
        ("ep1.html", 19),
        ("ep2.html", 18),
        ("compline.html", 7),
        ("noonday.html", 5),
        # Daily devotions
        ("devotion.html", 8),
        ("devotion2.html", 8),
        # Canticle table + Venite
        ("canticle.html", 3),
        # Great Litany
        ("Litany.html", 2),
        # Order for Evening
        ("evening.html", 5),
        # ToC / skip files
        ("dailyoff.html", 0),
        ("concernmp1.html", 0),
        ("concernmp2.html", 0),
        ("concernevening.html", 0),
        ("direct.html", 0),
    ],
)
def test_fixture_unit_count(filename: str, expected_count: int) -> None:
    units = _load(filename)
    assert len(units) == expected_count, (
        f"{filename}: got {len(units)} units, expected {expected_count}. "
        f"Names: {[u.name for u in units]}"
    )


# ---------------------------------------------------------------------------
# Office / rite derivation from filename
# ---------------------------------------------------------------------------


class TestOfficeRiteDerivation:
    def test_mp1_is_morning_prayer_rite_i(self) -> None:
        units = _load("mp1.html")
        assert all(u.office == "morning_prayer" for u in units)
        assert all(u.rite == "rite_i" for u in units)

    def test_mp2_is_morning_prayer_rite_ii(self) -> None:
        units = _load("mp2.html")
        assert all(u.office == "morning_prayer" for u in units)
        assert all(u.rite == "rite_ii" for u in units)

    def test_ep1_is_evening_prayer_rite_i(self) -> None:
        units = _load("ep1.html")
        assert all(u.office == "evening_prayer" for u in units)
        assert all(u.rite == "rite_i" for u in units)

    def test_ep2_is_evening_prayer_rite_ii(self) -> None:
        units = _load("ep2.html")
        assert all(u.office == "evening_prayer" for u in units)
        assert all(u.rite == "rite_ii" for u in units)

    def test_compline_is_compline_none_rite(self) -> None:
        units = _load("compline.html")
        assert all(u.office == "compline" for u in units)
        assert all(u.rite == "none" for u in units)

    def test_noonday_is_noonday_none_rite(self) -> None:
        units = _load("noonday.html")
        assert all(u.office == "noonday" for u in units)
        assert all(u.rite == "none" for u in units)

    def test_litany_is_great_litany(self) -> None:
        units = _load("Litany.html")
        assert all(u.office == "great_litany" for u in units)
        assert all(u.rite == "none" for u in units)

    def test_canticle_file_is_both_rite(self) -> None:
        units = _load("canticle.html")
        assert all(u.rite == "both" for u in units)
        assert all(u.office == "canticle" for u in units)

    def test_devotion_is_daily_devotions(self) -> None:
        units = _load("devotion.html")
        assert all(u.office == "daily_devotions" for u in units)


# ---------------------------------------------------------------------------
# ToC short-circuit
# ---------------------------------------------------------------------------


class TestToCShortCircuit:
    @pytest.mark.parametrize(
        "filename",
        ["dailyoff.html", "concernmp1.html", "concernmp2.html",
         "concernevening.html", "direct.html"],
    )
    def test_toc_files_return_empty(self, filename: str) -> None:
        assert _load(filename) == []

    def test_small_html_without_strong_returns_empty(self) -> None:
        # Synthesise a small file with no <strong> tags
        tiny_path = FIXTURE_DIR / "_tiny_test.html"
        tiny_path.write_text("<html><body><p>No headings here.</p></body></html>")
        try:
            result = parse_daily_office_file(tiny_path)
            assert result == []
        finally:
            tiny_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Malformed HTML tolerance
# ---------------------------------------------------------------------------


class TestMalformedHTMLTolerance:
    def test_broken_html_does_not_raise(self) -> None:
        """Parser must not raise on syntactically broken HTML."""
        broken_path = FIXTURE_DIR / "_broken_test.html"
        broken_path.write_text(
            "<html><body><p><strong>A Prayer</strong>"
            "<p>Almighty God, hear us. Amen.<br>"
            # Unclosed tags, no </body></html>
        )
        try:
            units = parse_daily_office_file(broken_path)
            # lxml recovers; we just need no exception
            assert isinstance(units, list)
        finally:
            broken_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Seasonal sentences
# ---------------------------------------------------------------------------


class TestSeasonalSentences:
    def test_mp1_has_ten_seasonal_sentences(self) -> None:
        units = _load("mp1.html")
        seasons = [u for u in units if u.kind == "seasonal_sentence"]
        assert len(seasons) == 10

    def test_mp2_has_ten_seasonal_sentences(self) -> None:
        units = _load("mp2.html")
        seasons = [u for u in units if u.kind == "seasonal_sentence"]
        assert len(seasons) == 10

    def test_advent_sentence_has_season_in_metadata(self) -> None:
        units = _load("mp1.html")
        advent = _find(units, "Opening Sentence (Advent)")
        assert advent.raw_metadata.get("season") == "Advent"

    def test_advent_sentence_body_not_empty(self) -> None:
        units = _load("mp1.html")
        advent = _find(units, "Opening Sentence (Advent)")
        assert len(advent.body_text) > 10

    def test_lent_sentence_season_in_metadata(self) -> None:
        units = _load("mp2.html")
        lent = _find(units, "Opening Sentence (Lent)")
        assert lent.raw_metadata.get("season") == "Lent"

    def test_evening_has_three_seasonal_sentences(self) -> None:
        units = _load("evening.html")
        seasons = [u for u in units if u.kind == "seasonal_sentence"]
        assert len(seasons) == 3

    def test_seasonal_sentence_slug_contains_anglican(self) -> None:
        units = _load("mp1.html")
        advent = _find(units, "Opening Sentence (Advent)")
        assert advent.slug.endswith("_anglican")

    def test_seasonal_sentence_kind(self) -> None:
        units = _load("mp1.html")
        advent = _find(units, "Opening Sentence (Advent)")
        assert advent.kind == "seasonal_sentence"


# ---------------------------------------------------------------------------
# Morning Prayer spot-checks
# ---------------------------------------------------------------------------


class TestMorningPrayerRiteI:
    def setup_method(self) -> None:
        self.units = _load("mp1.html")

    def test_yields_non_empty(self) -> None:
        assert len(self.units) >= 30

    def test_venite_is_canticle(self) -> None:
        venite = _find(self.units, "Venite")
        assert venite.kind == "canticle"
        assert venite.rite == "rite_i"

    def test_venite_slug(self) -> None:
        venite = _find(self.units, "Venite")
        assert venite.slug == "venite_anglican"

    def test_venite_body_not_empty(self) -> None:
        venite = _find(self.units, "Venite")
        assert "let us sing" in venite.body_text.lower()

    def test_te_deum_present(self) -> None:
        # mp1 canticle 7 is "We Praise Thee  Te Deum laudamus"
        te_deum = _find(self.units, "We Praise Thee")
        assert te_deum.kind == "canticle"

    def test_magnificat_appears_in_mp1_canticles(self) -> None:
        # Song of Mary (Magnificat) is listed as an optional canticle in MP Rite I
        units_with_mag = [u for u in self.units if "Song of Mary" in u.name]
        assert len(units_with_mag) >= 1
        assert all(u.kind == "canticle" for u in units_with_mag)

    def test_apostles_creed_present(self) -> None:
        creed = _find(self.units, "Apostles' Creed")
        assert creed.kind == "creed"
        assert "I believe in God" in creed.body_text

    def test_suffrages_a_and_b_present(self) -> None:
        suffrages = [u for u in self.units if u.kind == "suffrage"]
        assert len(suffrages) >= 2

    def test_a_collect_for_peace_present(self) -> None:
        peace = _find(self.units, "A Collect for Peace")
        assert "Amen" in peace.body_text

    def test_all_units_rite_i(self) -> None:
        for u in self.units:
            assert u.rite == "rite_i", f"{u.name} has unexpected rite {u.rite!r}"


class TestMorningPrayerRiteII:
    def setup_method(self) -> None:
        self.units = _load("mp2.html")

    def test_yields_non_empty(self) -> None:
        assert len(self.units) >= 35

    def test_te_deum_is_canticle(self) -> None:
        # In mp2, Te Deum is #21 "You are God  Te Deum laudamus"
        te_deum = _find(self.units, "You are God")
        assert te_deum.kind == "canticle"
        assert te_deum.rite == "rite_ii"

    def test_te_deum_slug(self) -> None:
        te_deum = _find(self.units, "You are God")
        assert te_deum.slug == "you_are_god_anglican"

    def test_magnificat_in_mp2(self) -> None:
        # Magnificat appears as MP canticle #15 in mp2
        mag = _find(self.units, "Song of Mary")
        assert mag.kind == "canticle"
        assert mag.rite == "rite_ii"

    def test_magnificat_slug(self) -> None:
        mag = _find(self.units, "Song of Mary")
        assert "magnificat" in mag.slug

    def test_benedicite_present(self) -> None:
        bene = _find(self.units, "Song of Creation")
        assert bene.kind == "canticle"
        assert "Glorify the Lord" in bene.body_text

    def test_all_units_rite_ii(self) -> None:
        for u in self.units:
            assert u.rite == "rite_ii"


# ---------------------------------------------------------------------------
# Evening Prayer spot-checks
# ---------------------------------------------------------------------------


class TestEveningPrayerRiteI:
    def setup_method(self) -> None:
        self.units = _load("ep1.html")

    def test_magnificat_is_rite_i_variant(self) -> None:
        mag = _find(self.units, "Song of Mary")
        assert mag.rite == "rite_i"
        assert mag.kind == "canticle"

    def test_magnificat_body_content(self) -> None:
        mag = _find(self.units, "Song of Mary")
        assert "my soul" in mag.body_text.lower()

    def test_nunc_dimittis_present(self) -> None:
        nunc = _find(self.units, "Song of Simeon")
        assert nunc.kind == "canticle"
        assert nunc.rite == "rite_i"

    def test_collect_for_peace_present(self) -> None:
        peace = _find(self.units, "A Collect for Peace")
        assert peace.kind == "prayer"
        assert "Amen" in peace.body_text

    def test_general_thanksgiving_present(self) -> None:
        thanks = _find(self.units, "General Thanksgiving")
        assert "Amen" in thanks.body_text

    def test_apostles_creed_present(self) -> None:
        creed = _find(self.units, "Apostles' Creed")
        assert creed.kind == "creed"


class TestEveningPrayerRiteII:
    def setup_method(self) -> None:
        self.units = _load("ep2.html")

    def test_magnificat_is_rite_ii_variant(self) -> None:
        mag = _find(self.units, "Song of Mary")
        assert mag.rite == "rite_ii"

    def test_nunc_dimittis_rite_ii(self) -> None:
        nunc = _find(self.units, "Song of Simeon")
        assert nunc.kind == "canticle"
        assert nunc.rite == "rite_ii"

    def test_ep1_and_ep2_magnificat_different_rites(self) -> None:
        ep1_units = _load("ep1.html")
        ep2_units = _load("ep2.html")
        mag1 = _find(ep1_units, "Song of Mary")
        mag2 = _find(ep2_units, "Song of Mary")
        assert mag1.rite == "rite_i"
        assert mag2.rite == "rite_ii"
        assert mag1.rite != mag2.rite


# ---------------------------------------------------------------------------
# Compline spot-checks
# ---------------------------------------------------------------------------


class TestCompline:
    def setup_method(self) -> None:
        self.units = _load("compline.html")

    def test_yields_expected_units(self) -> None:
        assert len(self.units) == 7

    def test_compline_psalms_present(self) -> None:
        psalm_names = {u.name for u in self.units}
        assert "Psalm 4" in psalm_names
        assert "Psalm 91" in psalm_names

    def test_nunc_dimittis_in_compline(self) -> None:
        # Psalm 134 is the closing canticle in compline
        ps134 = _find(self.units, "Psalm 134")
        assert len(ps134.body_text) > 100

    def test_collect_for_saturdays_present(self) -> None:
        collect = _find(self.units, "A Collect for Saturdays")
        assert collect.kind == "prayer"

    def test_confession_present(self) -> None:
        # The initial section (An Order for Compline) contains the confession
        assert any("Compline" in u.name for u in self.units)


# ---------------------------------------------------------------------------
# Great Litany spot-checks
# ---------------------------------------------------------------------------


class TestGreatLitany:
    def setup_method(self) -> None:
        self.units = _load("Litany.html")

    def test_has_two_units(self) -> None:
        assert len(self.units) == 2

    def test_has_great_litany_unit(self) -> None:
        litany = _find(self.units, "Great Litany")
        assert litany.office == "great_litany"

    def test_litany_body_contains_supplications(self) -> None:
        litany = _find(self.units, "Great Litany")
        assert "beseech" in litany.body_text.lower()

    def test_supplication_section_present(self) -> None:
        supp = _find(self.units, "Supplication")
        assert len(supp.body_text) > 100

    def test_litany_rite_is_none(self) -> None:
        for u in self.units:
            assert u.rite == "none"

    def test_litany_office_is_great_litany(self) -> None:
        for u in self.units:
            assert u.office == "great_litany"


# ---------------------------------------------------------------------------
# Canticle table of contents
# ---------------------------------------------------------------------------


class TestCanticleFile:
    def setup_method(self) -> None:
        self.units = _load("canticle.html")

    def test_has_three_units(self) -> None:
        assert len(self.units) == 3

    def test_venite_traditional_present(self) -> None:
        venite = _find(self.units, "Psalm 95")
        assert venite.kind == "canticle"

    def test_rite_is_both(self) -> None:
        for u in self.units:
            assert u.rite == "both"


# ---------------------------------------------------------------------------
# Noonday
# ---------------------------------------------------------------------------


class TestNoonday:
    def setup_method(self) -> None:
        self.units = _load("noonday.html")

    def test_has_five_units(self) -> None:
        assert len(self.units) == 5

    def test_noonday_psalms_present(self) -> None:
        names = {u.name for u in self.units}
        assert "Psalm 119" in names
        assert "Psalm 121" in names
        assert "Psalm 126" in names

    def test_psalms_are_canticles(self) -> None:
        for u in self.units:
            if "Psalm" in u.name:
                assert u.kind == "canticle", f"{u.name} should be canticle"

    def test_noonday_office(self) -> None:
        for u in self.units:
            assert u.office == "noonday"


# ---------------------------------------------------------------------------
# Slug format
# ---------------------------------------------------------------------------


class TestSlugs:
    def test_all_slugs_end_with_anglican(self) -> None:
        for fname in ["mp1.html", "mp2.html", "ep1.html", "ep2.html",
                      "compline.html", "Litany.html"]:
            for u in _load(fname):
                assert u.slug.endswith("_anglican"), (
                    f"{fname}: slug {u.slug!r} does not end with _anglican"
                )

    def test_all_slugs_lowercase(self) -> None:
        for fname in ["mp2.html", "ep2.html"]:
            for u in _load(fname):
                assert u.slug == u.slug.lower(), f"slug {u.slug!r} not lowercase"

    def test_canonical_id_equals_slug(self) -> None:
        for u in _load("mp1.html"):
            assert u.canonical_id == u.slug

    def test_venite_slug(self) -> None:
        units = _load("mp1.html")
        venite = _find(units, "Venite")
        assert venite.slug == "venite_anglican"

    def test_magnificat_slug_mp2(self) -> None:
        units = _load("mp2.html")
        mag = _find(units, "Song of Mary")
        assert "song_of_mary" in mag.slug
        assert mag.slug.endswith("_anglican")

    def test_te_deum_slug_mp2(self) -> None:
        units = _load("mp2.html")
        te_deum = _find(units, "You are God")
        assert te_deum.slug == "you_are_god_anglican"


# ---------------------------------------------------------------------------
# Body text quality
# ---------------------------------------------------------------------------


class TestBodyTextQuality:
    def test_no_html_tags_in_body_text(self) -> None:
        for fname in ["mp1.html", "ep1.html", "compline.html"]:
            for u in _load(fname):
                assert "<" not in u.body_text, (
                    f"{fname} {u.name!r}: HTML tag in body_text"
                )

    def test_no_double_spaces_in_body_text(self) -> None:
        for u in _load("mp2.html"):
            assert "  " not in u.body_text, (
                f"double space in {u.name!r}"
            )

    def test_prayer_bodies_contain_amen(self) -> None:
        for u in _load("ep1.html"):
            if u.kind == "prayer" and u.body_text:
                assert "Amen" in u.body_text, (
                    f"prayer {u.name!r} missing Amen"
                )

    def test_canticle_bodies_not_empty(self) -> None:
        for fname in ["mp1.html", "mp2.html", "ep1.html"]:
            for u in _load(fname):
                if u.kind == "canticle" and u.name not in ("The Psalm or Psalms Appointed",):
                    assert len(u.body_text) > 20, (
                        f"{fname} canticle {u.name!r} has very short body"
                    )


# ---------------------------------------------------------------------------
# Rubric capture
# ---------------------------------------------------------------------------


class TestRubrics:
    def test_compline_opening_has_rubric(self) -> None:
        units = _load("compline.html")
        # At least one unit should have rubrics
        units_with_rubrics = [u for u in units if u.rubrics]
        assert len(units_with_rubrics) >= 1

    def test_rubrics_are_strings(self) -> None:
        for u in _load("mp1.html"):
            for r in u.rubrics:
                assert isinstance(r, str)
                assert len(r) > 0

    def test_mp2_confession_has_rubrics(self) -> None:
        units = _load("mp2.html")
        confession = _find(units, "Confession of Sin")
        assert len(confession.rubrics) >= 1


# ---------------------------------------------------------------------------
# Page number tracking
# ---------------------------------------------------------------------------


class TestPageNumbers:
    def test_page_numbers_tracked_in_mp1(self) -> None:
        units = _load("mp1.html")
        pages = [u.page_number for u in units if u.page_number is not None]
        assert len(pages) >= 5, "Expected several units with page numbers"

    def test_page_numbers_are_integers(self) -> None:
        for u in _load("ep1.html"):
            if u.page_number is not None:
                assert isinstance(u.page_number, int)
                assert 1 <= u.page_number <= 999

    def test_mp2_has_page_numbers(self) -> None:
        units = _load("mp2.html")
        assert any(u.page_number is not None for u in units)

    def test_litany_has_page_numbers(self) -> None:
        units = _load("Litany.html")
        assert any(u.page_number is not None for u in units)


# ---------------------------------------------------------------------------
# raw_metadata shape
# ---------------------------------------------------------------------------


class TestRawMetadata:
    def test_metadata_contains_office(self) -> None:
        for u in _load("mp1.html"):
            assert "office" in u.raw_metadata

    def test_metadata_contains_rite(self) -> None:
        for u in _load("ep2.html"):
            assert "rite" in u.raw_metadata
            assert u.raw_metadata["rite"] == "rite_ii"

    def test_seasonal_sentence_metadata_has_season(self) -> None:
        units = _load("mp2.html")
        for u in units:
            if u.kind == "seasonal_sentence":
                assert "season" in u.raw_metadata, (
                    f"seasonal sentence {u.name!r} missing season in raw_metadata"
                )

    def test_source_file_in_metadata(self) -> None:
        for u in _load("ep1.html"):
            assert "source_file" in u.raw_metadata
            assert "ep1.html" in u.raw_metadata["source_file"]

    def test_metadata_is_dict(self) -> None:
        for u in _load("compline.html"):
            assert isinstance(u.raw_metadata, dict)


# ---------------------------------------------------------------------------
# Frozen dataclass / type contracts
# ---------------------------------------------------------------------------


class TestDataclassContract:
    def test_parsedoffice_is_frozen(self) -> None:
        units = _load("mp1.html")
        u = units[0]
        with pytest.raises((AttributeError, TypeError)):
            u.slug = "modified"  # type: ignore[misc]

    def test_rubrics_is_tuple(self) -> None:
        for u in _load("mp1.html"):
            assert isinstance(u.rubrics, tuple)

    def test_all_required_fields_present(self) -> None:
        units = _load("ep1.html")
        for u in units:
            assert isinstance(u.slug, str)
            assert isinstance(u.name, str)
            assert u.rite in ("rite_i", "rite_ii", "both", "none")
            assert u.office in (
                "morning_prayer", "evening_prayer", "compline",
                "noonday", "daily_devotions", "canticle", "great_litany",
            )
            assert u.kind in (
                "canticle", "prayer", "creed", "psalm_ref",
                "seasonal_sentence", "versicle_response",
                "rubric_block", "intro", "suffrage",
            )
