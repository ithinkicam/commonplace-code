"""Tests for Pocket Casts share-link resolution in commonplace_worker.handlers.podcast."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from commonplace_worker.handlers.podcast import (
    _extract_title_from_html,
    _find_enclosure_in_rss,
    _find_meta_content,
    _is_pocketcasts_url,
    _itunes_search_feed_url,
    _normalise_episode_title,
    resolve_pocketcasts_url,
)

# ---------------------------------------------------------------------------
# Domain detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://pca.st/episode/abc", True),
        ("https://www.pca.st/episode/abc", True),
        ("https://pocketcasts.com/podcast/foo/bar", True),
        ("https://www.pocketcasts.com/podcast/foo/bar", True),
        ("https://youtube.com/watch?v=abc", False),
        ("https://open.spotify.com/episode/abc", False),
        ("https://podcasts.apple.com/us/podcast/x/id1", False),
        ("not a url", False),
    ],
)
def test_is_pocketcasts_url(url: str, expected: bool) -> None:
    assert _is_pocketcasts_url(url) is expected


# ---------------------------------------------------------------------------
# Meta tag extraction — tolerates Pocket Casts' data-rh attribute + any order
# ---------------------------------------------------------------------------


def test_find_meta_content_handles_data_rh_prefix() -> None:
    html = '<meta data-rh="true" name="twitter:title" content="Hello &amp; World"/>'
    assert _find_meta_content(html, "name", "twitter:title") == "Hello & World"


def test_find_meta_content_handles_property_attribute() -> None:
    html = '<meta data-rh="true" property="og:title" content="My Episode"/>'
    assert _find_meta_content(html, "property", "og:title") == "My Episode"


def test_find_meta_content_returns_none_on_miss() -> None:
    assert _find_meta_content("<html>no meta</html>", "name", "twitter:title") is None


def test_find_meta_content_unescapes_html_entities() -> None:
    html = '<meta name="twitter:title" content="Q&amp;A: &#8217;s"/>'
    assert _find_meta_content(html, "name", "twitter:title") == "Q&A: ’s"


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------


def test_normalise_episode_title_strips_punctuation_and_lowercases() -> None:
    assert (
        _normalise_episode_title("1. Narcissus & Echo - The Influencer")
        == "1 narcissus echo the influencer"
    )


def test_normalise_episode_title_collapses_whitespace() -> None:
    assert _normalise_episode_title("  A   B\n\nC ") == "a b c"


# ---------------------------------------------------------------------------
# iTunes search — mocked
# ---------------------------------------------------------------------------


def test_itunes_search_returns_first_feed_url() -> None:
    fake_json = {
        "resultCount": 2,
        "results": [
            {"collectionName": "Show 1", "feedUrl": "https://feed1.rss"},
            {"collectionName": "Show 2", "feedUrl": "https://feed2.rss"},
        ],
    }
    resp = MagicMock(status_code=200)
    resp.json.return_value = fake_json
    resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=resp):
        assert _itunes_search_feed_url("something") == "https://feed1.rss"


def test_itunes_search_returns_none_on_empty() -> None:
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"resultCount": 0, "results": []}
    resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=resp):
        assert _itunes_search_feed_url("nothing here") is None


def test_itunes_search_tolerates_network_error() -> None:
    with patch("httpx.get", side_effect=Exception("network down")):
        assert _itunes_search_feed_url("anything") is None


def test_itunes_search_skips_items_without_feed_url() -> None:
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "resultCount": 2,
        "results": [
            {"collectionName": "No feed"},
            {"collectionName": "Has feed", "feedUrl": "https://good.rss"},
        ],
    }
    resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=resp):
        assert _itunes_search_feed_url("x") == "https://good.rss"


# ---------------------------------------------------------------------------
# RSS enclosure matching
# ---------------------------------------------------------------------------


_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample Show</title>
    <item>
      <title>0. Welcome Episode</title>
      <enclosure url="https://cdn.example/0.mp3" type="audio/mpeg"/>
    </item>
    <item>
      <title>1. Narcissus &amp; Echo - The Influencer &amp; The Follower</title>
      <enclosure url="https://cdn.example/1.mp3" type="audio/mpeg"/>
    </item>
    <item>
      <title>2. Another Episode</title>
      <enclosure url="https://cdn.example/2.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>
"""


def test_find_enclosure_exact_normalised_match() -> None:
    got = _find_enclosure_in_rss(
        _SAMPLE_RSS,
        "1. Narcissus & Echo - The Influencer & The Follower",
    )
    assert got == "https://cdn.example/1.mp3"


def test_find_enclosure_ignores_case_and_punctuation() -> None:
    got = _find_enclosure_in_rss(
        _SAMPLE_RSS,
        "1. narcissus and echo the influencer and the follower",
    )
    # "and" is not in the feed title ("&" becomes empty in normalise), so
    # this will NOT exact-match, but it won't fuzzy-match either since the
    # normalised title has extra "and" tokens. Expect None — this documents
    # the matcher's strictness on conjunctions.
    assert got is None


def test_find_enclosure_fuzzy_substring_match() -> None:
    # Episode 1 in the feed; user-submitted title is a truncated form.
    got = _find_enclosure_in_rss(
        _SAMPLE_RSS,
        "Narcissus & Echo",
    )
    assert got == "https://cdn.example/1.mp3"


def test_find_enclosure_returns_none_on_no_match() -> None:
    assert _find_enclosure_in_rss(_SAMPLE_RSS, "some episode not in feed") is None


def test_find_enclosure_tolerates_bad_xml() -> None:
    assert _find_enclosure_in_rss("<<not xml>>", "anything") is None


# ---------------------------------------------------------------------------
# _extract_title_from_html — HTML entities + site-suffix stripping
# ---------------------------------------------------------------------------


def test_extract_title_returns_none_when_no_title_tag() -> None:
    assert _extract_title_from_html("<html><body>no title</body></html>") is None


def test_extract_title_unescapes_html_entities() -> None:
    html = "<html><head><title>Q&amp;A: Life &#8217;s meaning</title></head></html>"
    assert _extract_title_from_html(html) == "Q&A: Life ’s meaning"


def test_extract_title_strips_pocket_casts_suffix() -> None:
    html = (
        "<html><head><title>"
        "1. Narcissus &amp; Echo - The Influencer &amp; The Follower - Pocket Casts"
        "</title></head></html>"
    )
    assert (
        _extract_title_from_html(html)
        == "1. Narcissus & Echo - The Influencer & The Follower"
    )


def test_extract_title_strips_apple_podcasts_suffix() -> None:
    html = "<title>My Great Episode — Apple Podcasts</title>"
    assert _extract_title_from_html(html) == "My Great Episode"


def test_extract_title_strips_youtube_suffix() -> None:
    html = "<title>Cat Video - YouTube</title>"
    assert _extract_title_from_html(html) == "Cat Video"


def test_extract_title_leaves_unrelated_suffixes_alone() -> None:
    # "- Part 2" is not a known site-name suffix; must not be stripped.
    html = "<title>Serious Episode - Part 2</title>"
    assert _extract_title_from_html(html) == "Serious Episode - Part 2"


def test_extract_title_only_strips_trailing_suffix() -> None:
    # Matching substring mid-title must not be stripped.
    html = "<title>Pocket Casts changed the podcasting game</title>"
    assert (
        _extract_title_from_html(html)
        == "Pocket Casts changed the podcasting game"
    )


def test_extract_title_strips_case_sensitively() -> None:
    # The suffix list is case-sensitive; "pocket casts" lowercase inside
    # an episode title is not stripped. Documents the current trade-off
    # (preserves correctness over convenience).
    html = "<title>A meditation on pocket casts</title>"
    assert _extract_title_from_html(html) == "A meditation on pocket casts"


# ---------------------------------------------------------------------------
# End-to-end resolver — fully mocked
# ---------------------------------------------------------------------------


def test_resolve_pocketcasts_end_to_end_happy_path() -> None:
    # Mock the pca.st landing page and the RSS feed fetch via httpx.get,
    # and the iTunes search via the same. Three sequential calls:
    #   1) pca.st page → returns HTML with twitter:title meta + redirect URL
    #   2) iTunes search → returns one feed URL
    #   3) RSS feed fetch → returns the sample RSS above
    landing_html = (
        '<html><head>'
        '<meta data-rh="true" name="twitter:title" '
        'content="1. Narcissus &amp; Echo - The Influencer &amp; The Follower"/>'
        '</head></html>'
    )
    landing_resp = MagicMock()
    landing_resp.url = (
        "https://pocketcasts.com/podcast/my-show/"
        "SHOW-UUID/episode-slug/EP-UUID"
    )
    landing_resp.text = landing_html
    landing_resp.raise_for_status = MagicMock()

    itunes_resp = MagicMock()
    itunes_resp.raise_for_status = MagicMock()
    itunes_resp.json.return_value = {
        "resultCount": 1,
        "results": [{"feedUrl": "https://feed.example/rss"}],
    }

    rss_resp = MagicMock()
    rss_resp.text = _SAMPLE_RSS
    rss_resp.raise_for_status = MagicMock()

    with patch("httpx.get", side_effect=[landing_resp, itunes_resp, rss_resp]):
        got = resolve_pocketcasts_url("https://pca.st/episode/abc")
    assert got == "https://cdn.example/1.mp3"


def test_resolve_pocketcasts_returns_none_on_landing_fetch_failure() -> None:
    with patch("httpx.get", side_effect=Exception("blocked")):
        assert resolve_pocketcasts_url("https://pca.st/episode/abc") is None


def test_resolve_pocketcasts_returns_none_on_unexpected_redirect_path() -> None:
    resp = MagicMock()
    resp.url = "https://pocketcasts.com/some-other-path"
    resp.text = "<html></html>"
    resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=resp):
        assert resolve_pocketcasts_url("https://pca.st/episode/abc") is None


def test_resolve_pocketcasts_returns_none_when_no_meta_title() -> None:
    resp = MagicMock()
    resp.url = "https://pocketcasts.com/podcast/my-show/UUID/ep-slug/UUID"
    resp.text = "<html><head></head></html>"  # no meta tags
    resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=resp):
        assert resolve_pocketcasts_url("https://pca.st/episode/abc") is None


def test_resolve_pocketcasts_returns_none_when_itunes_returns_empty() -> None:
    landing_resp = MagicMock()
    landing_resp.url = "https://pocketcasts.com/podcast/unknown-show/UUID/ep/UUID"
    landing_resp.text = (
        '<meta name="twitter:title" content="Ep Title"/>'
    )
    landing_resp.raise_for_status = MagicMock()

    itunes_resp = MagicMock()
    itunes_resp.raise_for_status = MagicMock()
    itunes_resp.json.return_value = {"resultCount": 0, "results": []}

    with patch("httpx.get", side_effect=[landing_resp, itunes_resp]):
        assert resolve_pocketcasts_url("https://pca.st/episode/abc") is None
