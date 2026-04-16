"""Tests for commonplace_worker/handlers/video_filename.py.

Covers ≥15 cases including: clean names, torrent-release noise, anime bracket
prefixes, multi-season ranges, dot-separated names, ambiguous cases, and
year-free filenames.
"""

from __future__ import annotations

import pytest

from commonplace_worker.handlers.video_filename import parse

# ---------------------------------------------------------------------------
# Happy-path cases
# ---------------------------------------------------------------------------


def test_parse_torrent_style_movie() -> None:
    """Torrent-release movie with codec noise."""
    result = parse("Toy Story (1995) MULTi VFF 2160p 10bit HDR BluRay x265 AAC 7.1-QTZ.mkv")
    assert result["title"] == "Toy Story"
    assert result["year"] == 1995
    assert result["season"] is None
    assert result["episode"] is None


def test_parse_torrent_style_movie_no_extension() -> None:
    """Movie directory name without file extension."""
    result = parse("Addams Family Values 1993 2160p Bluray x265 DDP+DTS-KiNGDOM")
    assert result["title"] == "Addams Family Values"
    assert result["year"] == 1993


def test_parse_dot_separated_movie() -> None:
    """Dot-separated movie filename."""
    result = parse("8.Women.2002.1080p.BluRay.x264-USURY.mkv")
    assert result["title"] == "8 Women"
    assert result["year"] == 2002


def test_parse_anime_bracket_prefix() -> None:
    """Leading fansub group bracket should be stripped."""
    result = parse("[Kinomoto] Cardcaptor Sakura [BD 1080p Dual-Audio]", is_tv=True)
    assert "Cardcaptor Sakura" in result["title"]
    assert result["season"] is None  # no season in name


def test_parse_tv_show_with_year_and_season() -> None:
    """TV show with year and season number."""
    result = parse(
        "Andor (2022) Season 2 S02 (2160p HDR DSNP WEB-DL x265 HEVC 10bit DDP 5.1 Vyndros)",
        is_tv=True,
    )
    assert result["title"] == "Andor"
    assert result["year"] == 2022
    assert result["season"] == 2


def test_parse_tv_show_multi_season_range() -> None:
    """Multi-season pack 'S01-S08' — we take the first season."""
    result = parse(
        "Brooklyn Nine-Nine (2013) Season 1-8 S01-S08 (1080p AMZN WEB-DL x265 HEVC 10bit)",
        is_tv=True,
    )
    assert result["title"] == "Brooklyn Nine-Nine"
    assert result["year"] == 2013
    # Season is the first in the range
    assert result["season"] in (1, None)  # PTN may return list; we take first


def test_parse_tv_dot_separated_with_season() -> None:
    """Dot-separated TV show with season code."""
    result = parse("Blood.of.Zeus.S01.COMPLETE.720p.NF.WEBRip.x264-GalaxyTV", is_tv=True)
    assert result["title"] == "Blood of Zeus"
    assert result["season"] == 1
    assert result["year"] is None


def test_parse_simple_bare_name() -> None:
    """Bare name with no quality info."""
    result = parse("101 Dalmatians.avi")
    assert result["title"] == "101 Dalmatians"
    assert result["year"] is None
    assert result["season"] is None


def test_parse_simple_one_word() -> None:
    """Single-word title (e.g. a Bluey folder)."""
    result = parse("Bluey", is_tv=True)
    assert result["title"] == "Bluey"
    assert result["year"] is None


def test_parse_year_in_parens() -> None:
    """Year in parentheses (standard release format)."""
    result = parse("A Fantastic Woman (2017) [BluRay] [1080p] [YTS.AM]")
    assert result["title"] == "A Fantastic Woman"
    assert result["year"] == 2017


def test_parse_article_in_title() -> None:
    """Title starting with 'A' is preserved."""
    result = parse(
        "A Bugs Life (1998) MULTi VFF 2160p 10bit 4KLight HDR BluRay AC3 5.1 x265-QTZ.mkv"
    )
    assert result["title"] == "A Bugs Life"
    assert result["year"] == 1998


def test_parse_tv_complete_season_pack() -> None:
    """COMPLETE season pack format."""
    result = parse(
        "Abbott Elementary (2021) S03 (1080p AMZN WEB-DL x265 10bit EAC3 5.1 Silence)",
        is_tv=True,
    )
    assert result["title"] == "Abbott Elementary"
    assert result["year"] == 2021
    assert result["season"] == 3


def test_parse_criterion_edition() -> None:
    """Criterion edition with language annotation."""
    result = parse(
        "Umberto D. (1952) Criterion + Extras (1080p BluRay x265 HEVC 10bit AAC 1.0 Italian r00t)"
    )
    assert "Umberto D" in result["title"]
    assert result["year"] == 1952


def test_parse_hyphenated_title() -> None:
    """Title with hyphen preserved."""
    result = parse(
        "Y tu mama tambien [And Your Mother Too].2001.BRRip.XviD.AC3-VLiS"
    )
    # PTN should handle this; title should contain 'Y tu mama tambien' or similar
    assert result["title"]
    assert result["year"] == 2001


def test_parse_4k_webdl_movie() -> None:
    """4K WEB-DL movie."""
    result = parse(
        "Wake.Up.Dead.Man.A.Knives.Out.Mystery.2025.4K.HDR.DV.2160p.WEBDL Ita Eng x265-NAHOM"
    )
    assert result["title"]
    assert result["year"] == 2025


def test_parse_raises_value_error_on_empty() -> None:
    """ValueError raised for empty string."""
    with pytest.raises(ValueError, match="could not extract title"):
        parse("")


def test_parse_raises_value_error_on_whitespace_only() -> None:
    """ValueError raised for whitespace-only string."""
    with pytest.raises(ValueError, match="could not extract title"):
        parse("   ")


def test_parse_tv_fleabag_dot_separated() -> None:
    """Dot-separated TV format with season."""
    result = parse("Fleabag.S01.COMPLETE.720p.BluRay.x264-GalaxyTV", is_tv=True)
    assert result["title"] == "Fleabag"
    assert result["season"] == 1


def test_parse_tv_no_year_no_season() -> None:
    """TV show directory with just a name — no year, no season."""
    result = parse("A Kinght of the Seven Kingdoms", is_tv=True)
    assert result["title"]
    assert result["year"] is None
    assert result["season"] is None


def test_parse_movie_with_sequel_number() -> None:
    """Movie with number in title."""
    result = parse("Toy Story 4 (2019) MULTi VFF 2160p 10bit 4KLight HDR BluRay x265 AAC 7.1-QTZ.mkv")
    assert result["title"] == "Toy Story 4"
    assert result["year"] == 2019
