"""Tests for the BCP 1979 Psalter parser.

All tests run against trimmed fixture HTML files in
tests/fixtures/bcp_1979/psalter/ — no mocked HTML.

Full-file integration tests reference the live cache at
~/commonplace/cache/bcp_1979/www.bcponline.org/Psalter/the_psalter.html
and are skipped automatically when that cache is absent (e.g. CI).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commonplace_server.liturgical_parsers.bcp_psalter import (
    ParsedPsalm,
    PsalmVerse,
    parse_psalter_file,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "bcp_1979" / "psalter"

FULL_PSALTER = (
    Path.home() / "commonplace/cache/bcp_1979/www.bcponline.org/Psalter/the_psalter.html"
)
pytestmark_full = pytest.mark.skipif(
    not FULL_PSALTER.exists(), reason="live cache not present"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(filename: str) -> list[ParsedPsalm]:
    return parse_psalter_file(FIXTURE_DIR / filename)


def _find_psalm(psalms: list[ParsedPsalm], number: int) -> ParsedPsalm:
    for p in psalms:
        if p.number == number:
            return p
    raise AssertionError(f"Psalm {number} not found in results")


# ---------------------------------------------------------------------------
# ToC / skip-file tests
# ---------------------------------------------------------------------------


class TestSkipFiles:
    """Files that are not psalm content should return []."""

    def test_concerning_the_psalter_returns_empty(self) -> None:
        assert _load("concerning_the_psalter.html") == []

    def test_psalter_30day_returns_empty(self) -> None:
        assert _load("psalter_30day.html") == []

    def test_psalter_toc_returns_empty(self) -> None:
        assert _load("psalter.html") == []


# ---------------------------------------------------------------------------
# psalter_book_one_sample.html — Psalms 1, 2, 23
# ---------------------------------------------------------------------------


class TestBookOneSample:
    """Tests against the trimmed Book One fixture (Psalms 1, 2, 23)."""

    def setup_method(self) -> None:
        self.psalms = _load("psalter_book_one_sample.html")

    def test_psalm_count(self) -> None:
        assert len(self.psalms) == 3

    def test_psalm_numbers_present(self) -> None:
        nums = {p.number for p in self.psalms}
        assert nums == {1, 2, 23}

    def test_psalms_in_order(self) -> None:
        nums = [p.number for p in self.psalms]
        assert nums == sorted(nums)

    # ---- Psalm 1 ----

    def test_psalm_1_verse_count(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert len(p.verses) == 6

    def test_psalm_1_first_verse_text(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.verses[0].text.startswith("Happy are they")

    def test_psalm_1_first_verse_half_marker(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.verses[0].half_verse_marker is True

    def test_psalm_1_latin_incipit(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.latin_incipit == "Beatus vir qui non abiit"

    def test_psalm_1_slug(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.slug == "psalm_001_anglican"

    def test_psalm_1_title(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.title == "Psalm 1"

    def test_psalm_1_book(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.book == "one"

    def test_psalm_1_canonical_id_equals_slug(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.canonical_id == p.slug

    def test_psalm_1_no_subheadings(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.subheadings == ()

    def test_psalm_1_lord_small_caps_flattened(self) -> None:
        """The small-caps LORD span must be flattened to plain 'LORD'."""
        p = _find_psalm(self.psalms, 1)
        # Verse 6 contains "LORD"
        v6 = p.verses[5]
        assert "LORD" in v6.text
        # No residual HTML markup
        assert "<span" not in v6.text
        assert "small" not in v6.text

    def test_psalm_1_source_file(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.source_file == "psalter_book_one_sample.html"

    # ---- Psalm 2 ----

    def test_psalm_2_verse_count(self) -> None:
        p = _find_psalm(self.psalms, 2)
        assert len(p.verses) == 13

    def test_psalm_2_latin_incipit(self) -> None:
        p = _find_psalm(self.psalms, 2)
        assert p.latin_incipit == "Quare fremuerunt gentes?"

    def test_psalm_2_lord_in_verse(self) -> None:
        """Verse 2 of Psalm 2 contains 'LORD' (from small-caps span)."""
        p = _find_psalm(self.psalms, 2)
        v2 = p.verses[1]
        assert "LORD" in v2.text

    # ---- Psalm 23 ----

    def test_psalm_23_verse_count(self) -> None:
        p = _find_psalm(self.psalms, 23)
        assert len(p.verses) == 6

    def test_psalm_23_first_verse(self) -> None:
        p = _find_psalm(self.psalms, 23)
        assert p.verses[0].text.startswith("The LORD is my shepherd")

    def test_psalm_23_latin_incipit(self) -> None:
        p = _find_psalm(self.psalms, 23)
        assert p.latin_incipit == "Dominus regit me"

    def test_psalm_23_slug_zero_padded(self) -> None:
        p = _find_psalm(self.psalms, 23)
        assert p.slug == "psalm_023_anglican"
        # Verify format precisely — not psalm_23_anglican or psalm-23-anglican
        assert "psalm_023_" in p.slug

    def test_psalm_23_book_is_one(self) -> None:
        p = _find_psalm(self.psalms, 23)
        assert p.book == "one"

    def test_psalm_23_half_verse_markers(self) -> None:
        p = _find_psalm(self.psalms, 23)
        # Every verse in BCP psalter has a * caesura mark
        assert all(v.half_verse_marker for v in p.verses)

    def test_psalm_23_verse_texts_no_double_newlines(self) -> None:
        """Verse text should use single newlines, not double."""
        p = _find_psalm(self.psalms, 23)
        for v in p.verses:
            assert "\n\n" not in v.text


# ---------------------------------------------------------------------------
# psalter_119_sample.html — Psalm 119 alone
# ---------------------------------------------------------------------------


class TestPsalm119Sample:
    """Tests against the trimmed Psalm 119 fixture."""

    def setup_method(self) -> None:
        self.psalms = _load("psalter_119_sample.html")

    def test_exactly_one_psalm(self) -> None:
        assert len(self.psalms) == 1

    def test_psalm_number_is_119(self) -> None:
        assert self.psalms[0].number == 119

    def test_verse_count(self) -> None:
        """Psalm 119 has 176 verses (22 sections × 8 verses)."""
        assert len(self.psalms[0].verses) == 176

    def test_subheading_count(self) -> None:
        """22 Hebrew-letter subheadings."""
        assert len(self.psalms[0].subheadings) == 22

    def test_first_subheading_name(self) -> None:
        assert self.psalms[0].subheadings[0].text == "Aleph"

    def test_first_subheading_before_verse_1(self) -> None:
        assert self.psalms[0].subheadings[0].before_verse == 1

    def test_second_subheading_beth(self) -> None:
        assert self.psalms[0].subheadings[1].text == "Beth"

    def test_second_subheading_before_verse_9(self) -> None:
        """Beth section starts at verse 9."""
        assert self.psalms[0].subheadings[1].before_verse == 9

    def test_last_subheading_taw(self) -> None:
        assert self.psalms[0].subheadings[-1].text == "Taw"

    def test_last_subheading_before_verse_169(self) -> None:
        assert self.psalms[0].subheadings[-1].before_verse == 169

    def test_all_22_hebrew_letters_present(self) -> None:
        expected = {
            "Aleph", "Beth", "Gimel", "Daleth", "He", "Waw", "Zayin",
            "Heth", "Teth", "Yodh", "Kaph", "Lamedh", "Mem", "Nun",
            "Samekh", "Ayin", "Pe", "Sadhe", "Qoph", "Resh", "Shin", "Taw",
        }
        actual = {s.text for s in self.psalms[0].subheadings}
        assert actual == expected

    def test_first_verse_text(self) -> None:
        p = self.psalms[0]
        assert p.verses[0].text.startswith("Happy are they whose way is blameless")

    def test_psalm_119_book_is_five(self) -> None:
        assert self.psalms[0].book == "five"

    def test_psalm_119_slug(self) -> None:
        assert self.psalms[0].slug == "psalm_119_anglican"

    def test_each_section_has_8_verses(self) -> None:
        """Each of the 22 sections should have exactly 8 verses."""
        subs = self.psalms[0].subheadings
        verses = self.psalms[0].verses
        for i, sub in enumerate(subs):
            start = sub.before_verse
            end = subs[i + 1].before_verse if i + 1 < len(subs) else 177
            section_verses = [v for v in verses if start <= v.number < end]
            assert len(section_verses) == 8, (
                f"Section {sub.text} (v{start}–{end-1}) has {len(section_verses)} verses"
            )


# ---------------------------------------------------------------------------
# psalter_malformed_sample.html — lxml recovery
# ---------------------------------------------------------------------------


class TestMalformedSample:
    """lxml should recover from unclosed tags and still parse psalm content."""

    def setup_method(self) -> None:
        self.psalms = _load("psalter_malformed_sample.html")

    def test_parses_without_exception(self) -> None:
        assert isinstance(self.psalms, list)

    def test_psalm_1_extracted(self) -> None:
        assert len(self.psalms) >= 1
        assert self.psalms[0].number == 1

    def test_verse_count_at_least_one(self) -> None:
        assert len(self.psalms[0].verses) >= 1

    def test_lord_small_caps_flattened_in_malformed(self) -> None:
        """Even in the malformed fixture, LORD small-caps must be plain text."""
        p = self.psalms[0]
        # Verse 2 has L<span style="font-size: small">ORD</span>
        v2 = p.verses[1]
        assert "LORD" in v2.text
        assert "<span" not in v2.text


# ---------------------------------------------------------------------------
# Book assignment tests
# ---------------------------------------------------------------------------


class TestBookAssignment:
    """Book ranges: one=1–41, two=42–72, three=73–89, four=90–106, five=107–150."""

    def setup_method(self) -> None:
        self.psalms = _load("psalter_book_one_sample.html")

    def test_psalm_1_book_one(self) -> None:
        assert _find_psalm(self.psalms, 1).book == "one"

    def test_psalm_2_book_one(self) -> None:
        assert _find_psalm(self.psalms, 2).book == "one"

    def test_psalm_23_book_one(self) -> None:
        assert _find_psalm(self.psalms, 23).book == "one"

    @pytestmark_full
    def test_psalm_42_book_two(self) -> None:
        psalms = parse_psalter_file(FULL_PSALTER)
        assert _find_psalm(psalms, 42).book == "two"

    @pytestmark_full
    def test_psalm_73_book_three(self) -> None:
        psalms = parse_psalter_file(FULL_PSALTER)
        assert _find_psalm(psalms, 73).book == "three"

    @pytestmark_full
    def test_psalm_90_book_four(self) -> None:
        psalms = parse_psalter_file(FULL_PSALTER)
        assert _find_psalm(psalms, 90).book == "four"

    @pytestmark_full
    def test_psalm_107_book_five(self) -> None:
        psalms = parse_psalter_file(FULL_PSALTER)
        assert _find_psalm(psalms, 107).book == "five"

    @pytestmark_full
    def test_psalm_150_book_five(self) -> None:
        psalms = parse_psalter_file(FULL_PSALTER)
        assert _find_psalm(psalms, 150).book == "five"


# ---------------------------------------------------------------------------
# Slug format tests
# ---------------------------------------------------------------------------


class TestSlugFormat:
    """Slugs must be zero-padded to 3 digits."""

    def setup_method(self) -> None:
        self.psalms = _load("psalter_book_one_sample.html")

    def test_psalm_1_slug_three_digit_padding(self) -> None:
        p = _find_psalm(self.psalms, 1)
        assert p.slug == "psalm_001_anglican"

    def test_psalm_2_slug(self) -> None:
        p = _find_psalm(self.psalms, 2)
        assert p.slug == "psalm_002_anglican"

    def test_psalm_23_slug(self) -> None:
        p = _find_psalm(self.psalms, 23)
        assert p.slug == "psalm_023_anglican"

    def test_slug_not_with_hyphens(self) -> None:
        for p in self.psalms:
            assert "-" not in p.slug

    def test_slug_ends_with_anglican(self) -> None:
        for p in self.psalms:
            assert p.slug.endswith("_anglican")

    def test_canonical_id_equals_slug(self) -> None:
        for p in self.psalms:
            assert p.canonical_id == p.slug


# ---------------------------------------------------------------------------
# Dataclass shape tests
# ---------------------------------------------------------------------------


class TestDataclassShape:
    """Verify the dataclasses have expected fields."""

    def setup_method(self) -> None:
        self.psalms = _load("psalter_book_one_sample.html")
        self.p1 = _find_psalm(self.psalms, 1)

    def test_parsed_psalm_is_frozen(self) -> None:
        with pytest.raises(Exception):
            self.p1.number = 999  # type: ignore[misc]

    def test_verses_is_tuple(self) -> None:
        assert isinstance(self.p1.verses, tuple)

    def test_subheadings_is_tuple(self) -> None:
        assert isinstance(self.p1.subheadings, tuple)

    def test_verse_is_frozen(self) -> None:
        with pytest.raises(Exception):
            self.p1.verses[0].number = 999  # type: ignore[misc]

    def test_verse_fields(self) -> None:
        v = self.p1.verses[0]
        assert isinstance(v, PsalmVerse)
        assert isinstance(v.number, int)
        assert isinstance(v.text, str)
        assert isinstance(v.half_verse_marker, bool)

    def test_raw_metadata_is_dict(self) -> None:
        assert isinstance(self.p1.raw_metadata, dict)

    def test_source_anchor_present(self) -> None:
        # Psalm 1 has source_anchor="1"
        assert self.p1.source_anchor == "1"


# ---------------------------------------------------------------------------
# psday marker tests
# ---------------------------------------------------------------------------


class TestPsdayMarkers:
    """psday markers should be captured in raw_metadata."""

    @pytestmark_full
    def test_psalm_1_has_morning_prayer_marker(self) -> None:
        psalms = parse_psalter_file(FULL_PSALTER)
        p1 = _find_psalm(psalms, 1)
        assert "psday_before_verse_1" in p1.raw_metadata
        assert p1.raw_metadata["psday_before_verse_1"] == "First Day: Morning Prayer"

    @pytestmark_full
    def test_mid_psalm_psday_recorded(self) -> None:
        """Psalm 5 has an Evening Prayer marker mid-psalm."""
        psalms = parse_psalter_file(FULL_PSALTER)
        p5 = _find_psalm(psalms, 5)
        # Should have a psday marker partway through
        psday_keys = [k for k in p5.raw_metadata if k.startswith("psday_")]
        assert len(psday_keys) >= 1

    @pytestmark_full
    def test_psday_marker_keys_format(self) -> None:
        """psday keys should follow 'psday_before_verse_N' format."""
        psalms = parse_psalter_file(FULL_PSALTER)
        psalms_with_meta = [p for p in psalms if p.raw_metadata]
        for p in psalms_with_meta[:5]:
            for key in p.raw_metadata:
                assert key.startswith("psday_"), f"Unexpected key {key!r} in psalm {p.number}"


# ---------------------------------------------------------------------------
# Latin incipit tests
# ---------------------------------------------------------------------------


class TestLatinIncipit:
    """Latin incipits should be extracted on psalms that have them."""

    def setup_method(self) -> None:
        self.psalms = _load("psalter_book_one_sample.html")

    def test_psalm_1_latin(self) -> None:
        assert _find_psalm(self.psalms, 1).latin_incipit == "Beatus vir qui non abiit"

    def test_psalm_2_latin(self) -> None:
        assert _find_psalm(self.psalms, 2).latin_incipit == "Quare fremuerunt gentes?"

    def test_psalm_23_latin(self) -> None:
        assert _find_psalm(self.psalms, 23).latin_incipit == "Dominus regit me"

    def test_latin_has_no_trailing_whitespace(self) -> None:
        for p in self.psalms:
            if p.latin_incipit is not None:
                assert p.latin_incipit == p.latin_incipit.strip()
                assert not p.latin_incipit.endswith("\xa0")


# ---------------------------------------------------------------------------
# Full-file integration tests (skip if cache absent)
# ---------------------------------------------------------------------------


@pytestmark_full
class TestFullPsalter:
    """Integration tests against the full 920 KB psalter file."""

    def setup_method(self) -> None:
        self.psalms = parse_psalter_file(FULL_PSALTER)

    def test_total_psalm_count(self) -> None:
        assert len(self.psalms) == 150

    def test_all_psalm_numbers_1_to_150(self) -> None:
        nums = {p.number for p in self.psalms}
        assert nums == set(range(1, 151))

    def test_total_verse_count(self) -> None:
        total = sum(len(p.verses) for p in self.psalms)
        # BCP 1979 Psalter has 2527 total numbered verses but the online
        # source collapses some; empirically the file yields 2505.
        assert 2490 <= total <= 2530

    def test_psalm_1_six_verses(self) -> None:
        assert len(_find_psalm(self.psalms, 1).verses) == 6

    def test_psalm_23_six_verses(self) -> None:
        assert len(_find_psalm(self.psalms, 23).verses) == 6

    def test_psalm_119_176_verses(self) -> None:
        assert len(_find_psalm(self.psalms, 119).verses) == 176

    def test_psalm_119_22_subheadings(self) -> None:
        assert len(_find_psalm(self.psalms, 119).subheadings) == 22

    def test_psalm_150_six_verses(self) -> None:
        assert len(_find_psalm(self.psalms, 150).verses) == 6

    def test_slugs_are_unique(self) -> None:
        slugs = [p.slug for p in self.psalms]
        assert len(slugs) == len(set(slugs))

    def test_slugs_zero_padded(self) -> None:
        p1 = _find_psalm(self.psalms, 1)
        p10 = _find_psalm(self.psalms, 10)
        p100 = _find_psalm(self.psalms, 100)
        assert p1.slug == "psalm_001_anglican"
        assert p10.slug == "psalm_010_anglican"
        assert p100.slug == "psalm_100_anglican"

    def test_psalms_with_subheadings(self) -> None:
        """Psalms 18, 37, 78, 89, 105, 106, 107, 119 have Part I/II subheadings."""
        psalms_with_subs = {p.number for p in self.psalms if p.subheadings}
        assert 119 in psalms_with_subs
        # Some long psalms have Part I/II
        assert 18 in psalms_with_subs

    def test_psalm_64_parsed_despite_malformed_anchor(self) -> None:
        """Psalm 64 has a malformed id attribute in source; should still parse."""
        p64 = _find_psalm(self.psalms, 64)
        assert p64.number == 64
        assert len(p64.verses) > 0

    def test_psalm_138_parsed_despite_wrong_anchor(self) -> None:
        """Psalm 138 has id='3' (wrong) in source; psnum span is authoritative."""
        p138 = _find_psalm(self.psalms, 138)
        assert p138.number == 138
        assert len(p138.verses) > 0

    def test_all_psalms_have_verses(self) -> None:
        empty = [p.number for p in self.psalms if not p.verses]
        assert empty == [], f"Psalms with no verses: {empty}"

    def test_lord_flattened_in_psalm_2(self) -> None:
        p2 = _find_psalm(self.psalms, 2)
        # Several verses reference LORD
        lord_verses = [v for v in p2.verses if "LORD" in v.text]
        assert len(lord_verses) > 0
        for v in lord_verses:
            assert "<span" not in v.text

    def test_psday_markers_present(self) -> None:
        psalms_with_meta = [p for p in self.psalms if p.raw_metadata]
        assert len(psalms_with_meta) >= 30  # 30-day schedule = 60 markers

    def test_source_file_recorded(self) -> None:
        for p in self.psalms:
            assert p.source_file == "the_psalter.html"
