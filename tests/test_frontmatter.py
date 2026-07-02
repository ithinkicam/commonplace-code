"""Tests for commonplace_worker.frontmatter — YAML escaping + slugify."""

from __future__ import annotations

from commonplace_worker.frontmatter import slugify, yaml_escape


def test_yaml_escape_wraps_in_double_quotes() -> None:
    assert yaml_escape("hello") == '"hello"'


def test_yaml_escape_escapes_inner_double_quotes() -> None:
    assert yaml_escape('a "quoted" thing') == '"a \\"quoted\\" thing"'


def test_yaml_escape_escapes_backslashes() -> None:
    assert yaml_escape("a\\b") == '"a\\\\b"'


def test_yaml_escape_handles_empty() -> None:
    assert yaml_escape("") == '""'


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("Hello World") == "hello-world"


def test_slugify_collapses_runs_of_non_alnum() -> None:
    assert slugify("a  b___c!!!d") == "a-b-c-d"


def test_slugify_strips_leading_trailing_hyphens() -> None:
    assert slugify("---foo---") == "foo"


def test_slugify_max_len_trims_and_rstrips_hyphen() -> None:
    # max_len cuts mid-word; the rstrip removes a trailing hyphen that
    # would otherwise look ugly in a filename.
    assert slugify("foo-bar-baz-quux", max_len=8) == "foo-bar"


def test_slugify_uses_fallback_on_empty_input() -> None:
    assert slugify("") == "capture"
    assert slugify("   ", fallback="episode") == "episode"
    assert slugify("!!!", fallback="article") == "article"


def test_slugify_preserves_digits() -> None:
    assert slugify("Chapter 3: The End") == "chapter-3-the-end"


# ---------------------------------------------------------------------------
# render_embed_header
# ---------------------------------------------------------------------------


def test_embed_header_renders_all_present_fields() -> None:
    from commonplace_worker.frontmatter import render_embed_header

    result = render_embed_header(
        [
            ("Title", "On Creation and Gender"),
            ("Channel", "Sister Vassa Larin"),
            ("URL", "https://www.youtube.com/watch?v=abc"),
        ]
    )
    assert result == (
        "Title: On Creation and Gender\n"
        "Channel: Sister Vassa Larin\n"
        "URL: https://www.youtube.com/watch?v=abc\n\n"
    )


def test_embed_header_skips_none_and_blank_values() -> None:
    from commonplace_worker.frontmatter import render_embed_header

    result = render_embed_header(
        [
            ("Title", "Real Title"),
            ("Author", None),
            ("Date", ""),
            ("Channel", "   "),
            ("URL", "https://x"),
        ]
    )
    assert result == "Title: Real Title\nURL: https://x\n\n"


def test_embed_header_returns_empty_when_all_skipped() -> None:
    from commonplace_worker.frontmatter import render_embed_header

    assert render_embed_header([("Title", None), ("URL", "")]) == ""
    assert render_embed_header([]) == ""


def test_embed_header_preserves_field_order() -> None:
    from commonplace_worker.frontmatter import render_embed_header

    # Order of the iterable is the output order — callers control
    # precedence so the most-searched-for field can land first.
    result = render_embed_header(
        [
            ("URL", "https://x"),
            ("Title", "Some Title"),
        ]
    )
    assert result.splitlines()[0] == "URL: https://x"
    assert result.splitlines()[1] == "Title: Some Title"


def test_embed_header_strips_value_whitespace() -> None:
    from commonplace_worker.frontmatter import render_embed_header

    assert render_embed_header([("Title", "  padded  ")]) == "Title: padded\n\n"
