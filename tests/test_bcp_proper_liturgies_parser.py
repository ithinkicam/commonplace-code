"""Tests for the BCP 1979 Proper Liturgies parser.

All tests run against real fixture HTML files in
tests/fixtures/bcp_1979/proper_liturgies/ — no mocked HTML, no network.

Coverage targets per the task contract:
  ≥5 representative units per liturgy, exercising:
  - speaker-line (dialogue tables)
  - prayer-body (collect, absolution, exhortation)
  - psalm-verse (Ash Wednesday embedded Psalm 51)
  - rubric (stage direction / class="rubric")
  - optional-block flag (inline-styled imposition prayer)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from commonplace_server.liturgical_parsers.bcp_proper_liturgies import (
    ParsedLiturgyUnit,
    parse_proper_liturgies_dir,
    parse_proper_liturgy_file,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "bcp_1979" / "proper_liturgies"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(filename: str) -> list[ParsedLiturgyUnit]:
    return parse_proper_liturgy_file(FIXTURE_DIR / filename)


def _find(
    units: list[ParsedLiturgyUnit], *, kind: str | None = None, text: str | None = None
) -> ParsedLiturgyUnit:
    """Return first unit matching kind and/or text fragment in body_text."""
    for u in units:
        if kind is not None and u.kind != kind:
            continue
        if text is not None and text not in u.body_text:
            continue
        return u
    criteria = f"kind={kind!r}, text={text!r}"
    raise AssertionError(f"No unit matching {criteria} in {len(units)} units")


def _find_all(
    units: list[ParsedLiturgyUnit], *, kind: str | None = None, section: str | None = None
) -> list[ParsedLiturgyUnit]:
    result = []
    for u in units:
        if kind is not None and u.kind != kind:
            continue
        if section is not None and u.section != section:
            continue
        result.append(u)
    return result


# ---------------------------------------------------------------------------
# ToC / skip files
# ---------------------------------------------------------------------------


class TestSkipFiles:
    @pytest.mark.parametrize("filename", ["liturgies.html", "concernvigil.html"])
    def test_skip_files_return_empty(self, filename: str) -> None:
        units = _load(filename)
        assert units == []


# ---------------------------------------------------------------------------
# Ash Wednesday
# ---------------------------------------------------------------------------


class TestAshWednesday:
    def setup_method(self) -> None:
        self.units = _load("ashwed.html")

    def test_yields_units(self) -> None:
        assert len(self.units) >= 10

    def test_liturgy_name(self) -> None:
        for u in self.units:
            assert u.liturgy_name == "Ash Wednesday"

    def test_liturgy_slug(self) -> None:
        for u in self.units:
            assert u.liturgy_slug == "ash_wednesday_anglican"

    def test_source_file(self) -> None:
        for u in self.units:
            assert u.source_file == "ashwed.html"

    # --- Collect (prayer-body) ---

    def test_collect_body_present(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="Almighty and everlasting God, you hate nothing")
        assert "forgive the sins of all who are penitent" in collect.body_text

    def test_collect_contains_amen(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="Almighty and everlasting God, you hate nothing")
        assert "Amen" in collect.body_text

    # --- Rubric ---

    def test_rubric_present(self) -> None:
        rubric = _find(self.units, kind="rubric", text="Celebrant begins the liturgy")
        assert "Collect of the Day" in rubric.body_text

    def test_rubric_kind_correct(self) -> None:
        rubrics = _find_all(self.units, kind="rubric")
        assert len(rubrics) >= 3

    def test_rubric_silence_present(self) -> None:
        rubric = _find(self.units, kind="rubric", text="Silence is then kept")
        assert "kneeling" in rubric.body_text

    # --- Inline-styled optional block ---

    def test_optional_block_detected(self) -> None:
        optional = [u for u in self.units if u.raw_metadata.get("is_optional")]
        assert len(optional) >= 1, "Expected at least one optional block (Ash Wed imposition)"

    def test_optional_block_is_prayer_body(self) -> None:
        optional = [u for u in self.units if u.raw_metadata.get("is_optional")]
        for u in optional:
            assert u.kind == "prayer-body"

    def test_optional_block_text_content(self) -> None:
        optional = [u for u in self.units if u.raw_metadata.get("is_optional")][0]
        # Should contain the ashes prayer text
        assert "ashes" in optional.body_text.lower() or "dust" in optional.body_text.lower()

    # --- Embedded Psalm 51 ---

    def test_psalm_verses_present(self) -> None:
        psalm_units = _find_all(self.units, kind="psalm-verse")
        assert len(psalm_units) >= 10, f"Expected ≥10 psalm verses, got {len(psalm_units)}"

    def test_psalm_verse_number_in_metadata(self) -> None:
        verse_1 = _find(self.units, kind="psalm-verse", text="Have mercy on me, O God")
        assert verse_1.raw_metadata.get("psalm_number") == 51
        assert verse_1.raw_metadata.get("verse_number") == 1

    def test_psalm_verse_text_content(self) -> None:
        verse_11 = [
            u for u in self.units
            if u.kind == "psalm-verse" and u.raw_metadata.get("verse_number") == 11
        ]
        assert verse_11, "Psalm 51:11 not found"
        assert "Create in me a clean heart" in verse_11[0].body_text

    def test_psalm_verse_half_verse_asterisk_preserved(self) -> None:
        # Psalm 51 has asterisks marking half-verse caesura
        verse_1 = _find(self.units, kind="psalm-verse", text="Have mercy on me, O God")
        assert "*" in verse_1.body_text

    # --- Litany of Penitence (prayer-body with embedded response lines) ---

    def test_litany_body_present(self) -> None:
        litany = _find(self.units, kind="prayer-body", text="Most holy and merciful Father")
        assert "We confess to you" in litany.body_text

    def test_have_mercy_response_in_litany(self) -> None:
        # The litany's "Have mercy on us, Lord." responses appear in prayer-body units
        mercy = _find(self.units, kind="prayer-body", text="Have mercy on us, Lord")
        assert mercy is not None

    # --- Page numbers ---

    def test_page_numbers_tracked(self) -> None:
        paged = [u for u in self.units if u.page_number is not None]
        assert len(paged) >= 5

    def test_page_number_range(self) -> None:
        pages = [u.page_number for u in self.units if u.page_number is not None]
        assert min(pages) >= 260
        assert max(pages) <= 275

    # --- No HTML tags in body text ---

    def test_no_html_tags_in_body_text(self) -> None:
        for u in self.units:
            assert "<br" not in u.body_text, f"<br> in body_text of {u.name}"
            assert "<em>" not in u.body_text, f"<em> in body_text of {u.name}"

    # --- Raw metadata shape ---

    def test_raw_metadata_fields_present(self) -> None:
        for u in self.units:
            meta = u.raw_metadata
            assert "liturgy_name" in meta
            assert "kind" in meta
            assert "source_file" in meta


# ---------------------------------------------------------------------------
# Palm Sunday
# ---------------------------------------------------------------------------


class TestPalmSunday:
    def setup_method(self) -> None:
        self.units = _load("palmsunday.html")

    def test_yields_units(self) -> None:
        assert len(self.units) >= 10

    def test_liturgy_name(self) -> None:
        for u in self.units:
            assert u.liturgy_name == "Palm Sunday"

    def test_liturgy_slug(self) -> None:
        for u in self.units:
            assert u.liturgy_slug == "palm_sunday_anglican"

    # --- Speaker dialogue tables ---

    def test_speaker_lines_present(self) -> None:
        speakers = _find_all(self.units, kind="speaker-line")
        assert len(speakers) >= 5

    def test_celebrant_let_us_pray(self) -> None:
        cel = _find(self.units, kind="speaker-line", text="Let us pray.")
        assert cel.raw_metadata.get("speaker") == "Celebrant"

    def test_people_response(self) -> None:
        people = _find(self.units, kind="speaker-line", text="And also with you.")
        assert people.raw_metadata.get("speaker") == "People"

    def test_deacon_line(self) -> None:
        deacon = _find(self.units, kind="speaker-line", text="Let us go forth in peace.")
        assert deacon.raw_metadata.get("speaker") == "Deacon"

    def test_people_in_name_of_christ(self) -> None:
        response = _find(self.units, kind="speaker-line", text="In the name of Christ.")
        assert response.raw_metadata.get("speaker") == "People"

    # --- Collect / prayer-body ---

    def test_collect_assist_us(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="Assist us mercifully with your help")
        assert "Amen" in collect.body_text

    def test_collect_almighty_everliving(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="Almighty and everliving God")
        assert "tender love" in collect.body_text
        assert "Amen" in collect.body_text

    def test_palm_blessing_prayer(self) -> None:
        blessing = _find(self.units, kind="prayer-body", text="It is right to praise you, Almighty God")
        assert "Palm Sunday" not in blessing.body_text  # no leakage of page markers
        assert "Amen" in blessing.body_text

    # --- Rubric ---

    def test_rubric_present(self) -> None:
        rubric = _find(self.units, kind="rubric", text="congregation may gather")
        assert "procession" in rubric.body_text

    def test_section_liturgy_of_palms(self) -> None:
        liturgy_palms = [u for u in self.units if "Liturgy of the Palms" in u.section]
        assert len(liturgy_palms) >= 5

    def test_section_at_the_eucharist(self) -> None:
        eucharist = [u for u in self.units if "Eucharist" in u.section]
        assert len(eucharist) >= 1

    def test_no_html_in_body(self) -> None:
        for u in self.units:
            assert "<br" not in u.body_text


# ---------------------------------------------------------------------------
# Maundy Thursday
# ---------------------------------------------------------------------------


class TestMaundyThursday:
    def setup_method(self) -> None:
        self.units = _load("thursday.html")

    def test_yields_units(self) -> None:
        assert len(self.units) >= 4

    def test_liturgy_name(self) -> None:
        for u in self.units:
            assert u.liturgy_name == "Maundy Thursday"

    def test_liturgy_slug(self) -> None:
        for u in self.units:
            assert u.liturgy_slug == "maundy_thursday_anglican"

    def test_collect_almighty_father(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="Almighty Father, whose dear Son")
        assert "Sacrament of his Body and Blood" in collect.body_text
        assert "Amen" in collect.body_text

    def test_rubric_washing_of_feet(self) -> None:
        rubric = _find(self.units, kind="rubric", text="washing of feet")
        assert "Gospel" in rubric.body_text

    def test_foot_washing_anthem(self) -> None:
        anthem = _find(self.units, kind="prayer-body", text="Do you know what I")
        assert "example" in anthem.body_text

    def test_peace_gift(self) -> None:
        peace = _find(self.units, kind="prayer-body", text="Peace is my last gift")
        assert "world cannot give" in peace.body_text

    def test_preface_of_holy_week(self) -> None:
        preface = _find(self.units, kind="prayer-body", text="Preface of Holy Week")
        assert preface is not None

    def test_no_html_in_body(self) -> None:
        for u in self.units:
            assert "<br" not in u.body_text


# ---------------------------------------------------------------------------
# Good Friday
# ---------------------------------------------------------------------------


class TestGoodFriday:
    def setup_method(self) -> None:
        self.units = _load("friday.html")

    def test_yields_units(self) -> None:
        assert len(self.units) >= 20

    def test_liturgy_name(self) -> None:
        for u in self.units:
            assert u.liturgy_name == "Good Friday"

    def test_liturgy_slug(self) -> None:
        for u in self.units:
            assert u.liturgy_slug == "good_friday_anglican"

    # --- Opening collect ---

    def test_collect_of_the_day(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="Almighty God, we pray you graciously")
        assert "cross" in collect.body_text.lower()
        assert "Amen" in collect.body_text

    # --- Speaker table ---

    def test_speaker_line_blessed_be_god(self) -> None:
        speaker = _find(self.units, kind="speaker-line", text="Blessed be our God")
        assert speaker is not None

    def test_speaker_line_people_response(self) -> None:
        response = _find(self.units, kind="speaker-line", text="For ever and ever. Amen.")
        assert response.raw_metadata.get("speaker") == "People"

    # --- Solemn Collects section ---

    def test_solemn_collects_section_exists(self) -> None:
        solemn = _find_all(self.units, section="The Solemn Collects")
        assert len(solemn) >= 8

    def test_solemn_collect_holy_church(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="governed and sanctified")
        assert "Amen" in collect.body_text

    def test_solemn_collect_for_nations(self) -> None:
        # This collect is split across a page break; just verify the first part exists
        collect = _find(self.units, kind="prayer-body", text="kindle, we pray, in every heart")
        assert "peace" in collect.body_text.lower()

    def test_solemn_collect_for_those_who_suffer(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="comfort of all who sorrow")
        assert "Amen" in collect.body_text

    # --- Anthems ---

    def test_anthem_1_present(self) -> None:
        anthem1 = [u for u in self.units if "Anthem 1" in u.section]
        assert len(anthem1) >= 1

    def test_anthem_2_present(self) -> None:
        anthem2 = [u for u in self.units if "Anthem 2" in u.section]
        assert len(anthem2) >= 1

    def test_anthem_text_we_glory(self) -> None:
        anthem = _find(self.units, kind="prayer-body", text="We glory in your cross")
        assert "resurrection" in anthem.body_text

    # --- Final prayer ---

    def test_final_prayer_present(self) -> None:
        final = _find(self.units, kind="prayer-body", text="Lord Jesus Christ, Son of the living God")
        assert "Amen" in final.body_text

    def test_no_html_in_body(self) -> None:
        for u in self.units:
            assert "<br" not in u.body_text

    # --- Rubrics ---

    def test_rubric_ministers_enter_in_silence(self) -> None:
        rubric = _find(self.units, kind="rubric", text="ministers enter in silence")
        assert rubric is not None

    def test_rubric_silence_present(self) -> None:
        silence_rubrics = [u for u in self.units if u.kind == "rubric" and u.body_text.strip() == "Silence"]
        assert len(silence_rubrics) >= 3


# ---------------------------------------------------------------------------
# Holy Saturday
# ---------------------------------------------------------------------------


class TestHolySaturday:
    def setup_method(self) -> None:
        self.units = _load("saturday.html")

    def test_yields_units(self) -> None:
        assert len(self.units) >= 2

    def test_liturgy_name(self) -> None:
        for u in self.units:
            assert u.liturgy_name == "Holy Saturday"

    def test_liturgy_slug(self) -> None:
        for u in self.units:
            assert u.liturgy_slug == "holy_saturday_anglican"

    def test_collect_of_the_day(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="O God, Creator of heaven and earth")
        assert "crucified body" in collect.body_text
        assert "Amen" in collect.body_text

    def test_collect_third_day(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="coming of the third day")
        assert "newness of life" in collect.body_text

    def test_rubric_no_eucharist(self) -> None:
        rubric = _find(self.units, kind="rubric", text="no celebration of the Eucharist")
        assert rubric is not None

    def test_rubric_after_gospel(self) -> None:
        rubric = _find(self.units, kind="rubric", text="After the Gospel")
        assert "Anthem" in rubric.body_text

    def test_page_number_at_end_of_file(self) -> None:
        # The Holy Saturday page marker (283) appears after the final rubric,
        # so no content unit has a page_number set — but the parser must not
        # crash and the unit count must still be ≥ 2.
        assert len(self.units) >= 2


# ---------------------------------------------------------------------------
# The Great Vigil of Easter
# ---------------------------------------------------------------------------


class TestGreatVigilOfEaster:
    def setup_method(self) -> None:
        self.units = _load("EasterVigil.html")

    def test_yields_units(self) -> None:
        assert len(self.units) >= 50

    def test_liturgy_name(self) -> None:
        for u in self.units:
            assert u.liturgy_name == "The Great Vigil of Easter"

    def test_liturgy_slug(self) -> None:
        for u in self.units:
            assert u.liturgy_slug == "the_great_vigil_of_easter_anglican"

    # --- Sections ---

    def test_section_lighting_of_paschal_candle(self) -> None:
        candle = _find_all(self.units, section="The Lighting of the Paschal Candle")
        assert len(candle) >= 5

    def test_section_liturgy_of_the_word(self) -> None:
        word = _find_all(self.units, section="The Liturgy of the Word")
        assert len(word) >= 15

    def test_section_renewal_of_baptismal_vows(self) -> None:
        vows = _find_all(self.units, section="The Renewal of Baptismal Vows")
        assert len(vows) >= 5

    def test_section_at_the_eucharist(self) -> None:
        eucharist = _find_all(self.units, section="At the Eucharist")
        assert len(eucharist) >= 5

    # --- Speaker dialogue (Exsultet and vows) ---

    def test_speaker_light_of_christ(self) -> None:
        line = _find(self.units, kind="speaker-line", text="The light of Christ.")
        assert line is not None

    def test_speaker_thanks_be_to_god(self) -> None:
        line = _find(self.units, kind="speaker-line", text="Thanks be to God.")
        assert line.raw_metadata.get("speaker") == "People"

    def test_speaker_people_i_will(self) -> None:
        line = _find(self.units, kind="speaker-line", text="I will, with God's help.")
        assert line.raw_metadata.get("speaker") == "People"

    def test_speaker_celebrant_do_you_reaffirm(self) -> None:
        line = _find(self.units, kind="speaker-line", text="Do you reaffirm your renunciation")
        assert line.raw_metadata.get("speaker") == "Celebrant"

    def test_speaker_alleluia_christ_is_risen(self) -> None:
        line = _find(self.units, kind="speaker-line", text="Alleluia. Christ is risen.")
        assert line is not None

    # --- Inline-styled optional Exsultet sections ---

    def test_optional_exsultet_blocks(self) -> None:
        optional = [u for u in self.units if u.raw_metadata.get("is_optional")]
        assert len(optional) >= 2

    def test_optional_exsultet_text(self) -> None:
        opt = [u for u in self.units if u.raw_metadata.get("is_optional")]
        texts = " ".join(u.body_text for u in opt)
        assert "marvelous" in texts.lower() or "holy is this night" in texts.lower()

    # --- Vigil collects (prayer-body) ---

    def test_collect_creation(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="wonderfully created, and yet more wonderfully restored")
        assert "Amen" in collect.body_text

    def test_collect_flood(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="you have placed in the skies the sign of your covenant")
        assert "Amen" in collect.body_text

    def test_collect_red_sea(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="delivered by the power of your mighty arm")
        assert "Baptism" in collect.body_text

    def test_easter_collect_option_1(self) -> None:
        # The source HTML line-breaks "only-\nBegotten" → normalised to "only- begotten"
        collect = _find(self.units, kind="prayer-body", text="for our redemption gave your only")
        assert "Son" in collect.body_text
        assert "Amen" in collect.body_text

    def test_easter_collect_option_2(self) -> None:
        collect = _find(self.units, kind="prayer-body", text="made this most holy night to shine")
        assert "Amen" in collect.body_text

    # --- Rubric ---

    def test_rubric_fire_kindled(self) -> None:
        rubric = _find(self.units, kind="rubric", text="In the darkness, fire is kindled")
        assert rubric is not None

    def test_rubric_paschal_candle(self) -> None:
        rubric = _find(self.units, kind="rubric", text="Paschal Candle is then lighted")
        assert rubric is not None

    # --- Page numbers ---

    def test_page_numbers_tracked(self) -> None:
        paged = [u for u in self.units if u.page_number is not None]
        assert len(paged) >= 20

    def test_page_number_range(self) -> None:
        pages = [u.page_number for u in self.units if u.page_number is not None]
        assert min(pages) >= 284
        assert max(pages) <= 296

    # --- No HTML tags ---

    def test_no_html_in_body(self) -> None:
        for u in self.units:
            assert "<br" not in u.body_text, f"<br> in {u.name}"


# ---------------------------------------------------------------------------
# Slug contract
# ---------------------------------------------------------------------------


class TestSlugContract:
    """All slugs must follow the {name_snake}_anglican scheme."""

    @pytest.mark.parametrize(
        "filename",
        [
            "ashwed.html",
            "palmsunday.html",
            "thursday.html",
            "friday.html",
            "saturday.html",
            "EasterVigil.html",
        ],
    )
    def test_all_slugs_end_with_anglican(self, filename: str) -> None:
        units = _load(filename)
        for u in units:
            assert u.slug.endswith("_anglican"), (
                f"{filename}: slug {u.slug!r} does not end with '_anglican'"
            )

    @pytest.mark.parametrize(
        "filename",
        [
            "ashwed.html",
            "palmsunday.html",
            "thursday.html",
            "friday.html",
            "saturday.html",
            "EasterVigil.html",
        ],
    )
    def test_all_slugs_lowercase_alnum(self, filename: str) -> None:
        units = _load(filename)
        pattern = re.compile(r"^[a-z0-9_]+$")
        for u in units:
            assert pattern.match(u.slug), (
                f"{filename}: slug {u.slug!r} contains invalid characters"
            )

    def test_liturgy_slugs_correct(self) -> None:
        expected = {
            "ashwed.html": "ash_wednesday_anglican",
            "palmsunday.html": "palm_sunday_anglican",
            "thursday.html": "maundy_thursday_anglican",
            "friday.html": "good_friday_anglican",
            "saturday.html": "holy_saturday_anglican",
            "EasterVigil.html": "the_great_vigil_of_easter_anglican",
        }
        for fname, slug in expected.items():
            units = _load(fname)
            assert all(u.liturgy_slug == slug for u in units), (
                f"{fname}: expected liturgy_slug={slug!r}"
            )


# ---------------------------------------------------------------------------
# parse_proper_liturgies_dir
# ---------------------------------------------------------------------------


class TestParseProperLiturgiesDir:
    def test_returns_all_liturgy_units(self) -> None:
        all_units = parse_proper_liturgies_dir(FIXTURE_DIR)
        # Sum of all six liturgy file unit counts >= 200
        assert len(all_units) >= 200

    def test_no_skip_file_leakage(self) -> None:
        all_units = parse_proper_liturgies_dir(FIXTURE_DIR)
        source_files = {u.source_file for u in all_units}
        assert "liturgies.html" not in source_files
        assert "concernvigil.html" not in source_files

    def test_all_six_liturgies_represented(self) -> None:
        all_units = parse_proper_liturgies_dir(FIXTURE_DIR)
        slugs = {u.liturgy_slug for u in all_units}
        expected = {
            "ash_wednesday_anglican",
            "palm_sunday_anglican",
            "maundy_thursday_anglican",
            "good_friday_anglican",
            "holy_saturday_anglican",
            "the_great_vigil_of_easter_anglican",
        }
        assert expected == slugs, f"Missing: {expected - slugs}"

    def test_all_kinds_present(self) -> None:
        all_units = parse_proper_liturgies_dir(FIXTURE_DIR)
        kinds = {u.kind for u in all_units}
        assert "prayer-body" in kinds
        assert "rubric" in kinds
        assert "speaker-line" in kinds
        assert "psalm-verse" in kinds


# ---------------------------------------------------------------------------
# Fixture JSON parity (smoke-check fixtures match live parse)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_json,html_file",
    [
        ("ash_wednesday_anglican.json", "ashwed.html"),
        ("palm_sunday_anglican.json", "palmsunday.html"),
        ("maundy_thursday_anglican.json", "thursday.html"),
        ("good_friday_anglican.json", "friday.html"),
        ("holy_saturday_anglican.json", "saturday.html"),
        ("the_great_vigil_of_easter_anglican.json", "EasterVigil.html"),
    ],
)
def test_fixture_json_unit_count_matches_live(fixture_json: str, html_file: str) -> None:
    """The JSON fixture and the live parse must agree on unit count."""
    fixture_data = json.loads((FIXTURE_DIR / fixture_json).read_text(encoding="utf-8"))
    live_units = _load(html_file)
    assert len(fixture_data) == len(live_units), (
        f"{fixture_json}: fixture has {len(fixture_data)} units, "
        f"live parse has {len(live_units)}"
    )
