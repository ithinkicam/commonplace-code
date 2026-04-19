"""Unit tests for the per-category embed-string composers in
``commonplace_worker.handlers.liturgy_bcp`` (plan §2.7, option Y).

These tests exercise the pure composer functions with literal inputs —
no DB, no parsers, no embedder. They pin the expected shape of the
structural prefix that a short BCP unit is given before the nomic
embedder sees its body, so that regressions in the compose contract
surface deterministically.

Scope (task 4.8 pass 1):
  * collect              (liturgical_proper / collect)
  * daily_office         (liturgical_proper / prayer|canticle|creed|...)
  * psalter              (psalter / psalm)
  * proper_liturgy       (liturgical_proper / speaker_line|rubric|...)
  * prayer_thanksgiving  (devotional_manual / prayer|thanksgiving)

The handler wiring (closure over the parser's unit object → passed to
``embed_document`` via ``embed_text_override``) is covered by the existing
integration tests in ``test_liturgy_bcp_handler.py``; here we only pin
the string-composition contract.
"""

from __future__ import annotations

import pytest

from commonplace_worker.handlers.liturgy_bcp import (
    _humanize_kind,
    _humanize_office,
    _humanize_rite,
    compose_collect_embed,
    compose_daily_office_embed,
    compose_prayer_thanksgiving_embed,
    compose_proper_liturgy_embed,
    compose_psalter_embed,
)

# ---------------------------------------------------------------------------
# Humanizer helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rite_in", "expected"),
    [
        ("rite_i", "Rite I"),
        ("rite_ii", "Rite II"),
        ("both", None),
        ("none", None),
        (None, None),
        ("", None),
    ],
)
def test_humanize_rite(rite_in: str | None, expected: str | None) -> None:
    assert _humanize_rite(rite_in) == expected


@pytest.mark.parametrize(
    ("office_in", "expected"),
    [
        ("morning_prayer", "Morning Prayer"),
        ("evening_prayer", "Evening Prayer"),
        ("compline", "Compline"),
        ("noonday", "the Noonday Office"),
        ("daily_devotions", "Daily Devotions"),
        ("canticle", "the Canticles"),
        ("great_litany", "the Great Litany"),
        ("eucharist", "the Holy Eucharist"),
        (None, None),
        ("unknown_office", None),
    ],
)
def test_humanize_office(office_in: str | None, expected: str | None) -> None:
    assert _humanize_office(office_in) == expected


@pytest.mark.parametrize(
    ("kind_in", "expected"),
    [
        ("canticle", "Canticle"),
        ("prayer", "Prayer"),
        ("prayer_body", "Prayer"),
        ("creed", "Creed"),
        ("psalm_ref", "Psalm reference"),
        ("psalm_verse", "Psalm verse"),
        ("seasonal_sentence", "Seasonal sentence"),
        ("versicle_response", "Versicle and response"),
        ("rubric_block", "Rubric"),
        ("rubric", "Rubric"),
        ("intro", "Introduction"),
        ("suffrage", "Suffrage"),
        ("speaker_line", "Liturgical response"),
        ("collect", "Collect"),
        ("thanksgiving", "Thanksgiving"),
        # Unknown kind: fall back to titlecase of cleaned string
        ("some_unknown", "Some unknown"),
        ("hyphen-word", "Hyphen word"),
        (None, "Liturgical unit"),
        ("", "Liturgical unit"),
    ],
)
def test_humanize_kind(kind_in: str | None, expected: str) -> None:
    assert _humanize_kind(kind_in) == expected


# ---------------------------------------------------------------------------
# Collect composer
# ---------------------------------------------------------------------------


def test_compose_collect_embed_rite_ii_with_section() -> None:
    out = compose_collect_embed(
        name="The Epiphany",
        rite="rite_ii",
        section="holydays",
        body_text="O God, by the leading of a star you manifested your only Son to the peoples of the earth. Amen.",
    )
    assert out == (
        "Collect for The Epiphany (Anglican, Rite II). Propers for holydays.\n\n"
        "O God, by the leading of a star you manifested your only Son to the peoples of the earth. Amen."
    )


def test_compose_collect_embed_rite_i_various_section() -> None:
    out = compose_collect_embed(
        name="For Peace",
        rite="rite_i",
        section="various",
        body_text="O God, who art the author of peace and lover of concord... Amen.",
    )
    assert out.startswith(
        "Collect for For Peace (Anglican, Rite I). Propers for various.\n\n"
    )
    assert out.endswith("O God, who art the author of peace and lover of concord... Amen.")


def test_compose_collect_embed_no_section_drops_clause() -> None:
    out = compose_collect_embed(
        name="For Purity",
        rite="rite_ii",
        section="",
        body_text="Almighty God, to you all hearts are open...",
    )
    assert "Propers for" not in out
    assert out.startswith("Collect for For Purity (Anglican, Rite II).\n\n")


def test_compose_collect_embed_missing_rite_drops_clause() -> None:
    out = compose_collect_embed(
        name="A Generic Collect",
        rite="both",   # not rite_i / rite_ii
        section="common",
        body_text="Lord have mercy.",
    )
    assert out.startswith("Collect for A Generic Collect (Anglican). Propers for common.\n\n")


# ---------------------------------------------------------------------------
# Daily Office composer
# ---------------------------------------------------------------------------


def test_compose_daily_office_embed_canticle_mp_rite_ii() -> None:
    out = compose_daily_office_embed(
        name="The Song of Mary",
        kind="canticle",
        office="morning_prayer",
        rite="rite_ii",
        body_text="My soul proclaims the greatness of the Lord, my spirit rejoices in God my Savior.",
    )
    assert out == (
        'Canticle "The Song of Mary" from Morning Prayer (Anglican, Rite II).\n\n'
        "My soul proclaims the greatness of the Lord, my spirit rejoices in God my Savior."
    )


def test_compose_daily_office_embed_prayer_compline_none_rite() -> None:
    out = compose_daily_office_embed(
        name="The Lord's Prayer",
        kind="prayer",
        office="compline",
        rite="none",
        body_text="Our Father, who art in heaven...",
    )
    # rite=none → rite clause dropped
    assert out == (
        'Prayer "The Lord\'s Prayer" from Compline (Anglican).\n\n'
        "Our Father, who art in heaven..."
    )


def test_compose_daily_office_embed_creed_without_name_repeats_kind() -> None:
    # Kind-only fallback when name equals kind (e.g., the raw name is literally
    # "Creed" for a generic creed heading).
    out = compose_daily_office_embed(
        name="Creed",
        kind="creed",
        office="evening_prayer",
        rite="rite_i",
        body_text="I believe in one God...",
    )
    # name == kind_label ignoring case: do not quote
    assert out == "Creed from Evening Prayer (Anglican, Rite I).\n\nI believe in one God..."


def test_compose_daily_office_embed_unknown_office_drops_office_clause() -> None:
    out = compose_daily_office_embed(
        name="A Litany",
        kind="suffrage",
        office=None,
        rite="rite_ii",
        body_text="For all who...",
    )
    assert "from " not in out.split("\n\n")[0]
    assert out.startswith('Suffrage "A Litany" (Anglican, Rite II).\n\n')


# ---------------------------------------------------------------------------
# Psalter composer
# ---------------------------------------------------------------------------


def test_compose_psalter_embed_with_latin_incipit() -> None:
    out = compose_psalter_embed(
        number=1,
        title="Psalm 1",
        latin_incipit="Beatus vir qui non abiit",
        body_text="1 Happy are they who have not walked in the counsel of the wicked...",
    )
    assert out == (
        "Psalm 1 — Beatus vir qui non abiit (Book of Common Prayer Psalter).\n\n"
        "1 Happy are they who have not walked in the counsel of the wicked..."
    )


def test_compose_psalter_embed_no_incipit() -> None:
    out = compose_psalter_embed(
        number=23,
        title="Psalm 23",
        latin_incipit=None,
        body_text="1 The Lord is my shepherd; I shall not be in want.",
    )
    assert out == (
        "Psalm 23 (Book of Common Prayer Psalter).\n\n"
        "1 The Lord is my shepherd; I shall not be in want."
    )


def test_compose_psalter_embed_distinct_title_folded_in() -> None:
    # If title carries info beyond "Psalm N" (e.g., a short nickname) it is
    # included parenthetically when no Latin incipit is available.
    out = compose_psalter_embed(
        number=119,
        title="Psalm 119 (The Law)",
        latin_incipit=None,
        body_text="1 Happy are they whose way is blameless...",
    )
    assert out.startswith(
        "Psalm 119 (Psalm 119 (The Law)) (Book of Common Prayer Psalter).\n\n"
    )


# ---------------------------------------------------------------------------
# Proper Liturgy composer
# ---------------------------------------------------------------------------


def test_compose_proper_liturgy_embed_speaker_line() -> None:
    out = compose_proper_liturgy_embed(
        name="Celebrant",
        kind="speaker_line",
        liturgy_name="Ash Wednesday",
        section="Liturgy of the Word",
        body_text="Bless the Lord who forgives all our sins.",
    )
    assert out == (
        'Liturgical response "Celebrant" from the Ash Wednesday — Liturgy of the Word '
        "(Anglican).\n\n"
        "Bless the Lord who forgives all our sins."
    )


def test_compose_proper_liturgy_embed_rubric_section_matches_liturgy() -> None:
    # When section == liturgy_name, the trailing "— {section}" clause is
    # dropped to avoid "Ash Wednesday — Ash Wednesday".
    out = compose_proper_liturgy_embed(
        name="Rubric",
        kind="rubric",
        liturgy_name="Palm Sunday",
        section="Palm Sunday",
        body_text="The people stand.",
    )
    assert "Palm Sunday — Palm Sunday" not in out
    assert out == "Rubric from the Palm Sunday (Anglican).\n\nThe people stand."


def test_compose_proper_liturgy_embed_prayer_body_with_name() -> None:
    out = compose_proper_liturgy_embed(
        name="The Solemn Collects",
        kind="prayer_body",
        liturgy_name="Good Friday",
        section="The Liturgy",
        body_text="Let us pray for the holy Catholic Church of Christ throughout the world...",
    )
    assert out.startswith(
        'Prayer "The Solemn Collects" from the Good Friday — The Liturgy (Anglican).\n\n'
    )


# ---------------------------------------------------------------------------
# Prayer & Thanksgiving composer
# ---------------------------------------------------------------------------


def test_compose_prayer_thanksgiving_embed_prayer_with_section() -> None:
    out = compose_prayer_thanksgiving_embed(
        title="For the Human Family",
        genre="prayer",
        section_header="Prayers for National Life",
        body_text="O God, you made us in your own image and redeemed us through Jesus your Son.",
    )
    assert out == (
        "Prayer — For the Human Family (Prayers for National Life) "
        "(Book of Common Prayer).\n\n"
        "O God, you made us in your own image and redeemed us through Jesus your Son."
    )


def test_compose_prayer_thanksgiving_embed_thanksgiving_no_section() -> None:
    out = compose_prayer_thanksgiving_embed(
        title="A General Thanksgiving",
        genre="thanksgiving",
        section_header="",
        body_text="Accept, O Lord, our thanks and praise for all that you have done for us.",
    )
    assert out == (
        "Thanksgiving — A General Thanksgiving (Book of Common Prayer).\n\n"
        "Accept, O Lord, our thanks and praise for all that you have done for us."
    )


# ---------------------------------------------------------------------------
# Shape / contract invariants
# ---------------------------------------------------------------------------


def test_all_composers_end_with_body_text_verbatim() -> None:
    """The composed string always ends with ``\\n\\n{body_text}``: the
    structural prefix never mutates the raw body text."""
    body = "BODY_SENTINEL_12345"

    assert compose_collect_embed(
        name="X", rite="rite_ii", section="common", body_text=body,
    ).endswith("\n\n" + body)
    assert compose_daily_office_embed(
        name="X", kind="prayer", office="compline", rite="rite_ii", body_text=body,
    ).endswith("\n\n" + body)
    assert compose_psalter_embed(
        number=1, title="Psalm 1", latin_incipit="Beatus vir", body_text=body,
    ).endswith("\n\n" + body)
    assert compose_proper_liturgy_embed(
        name="X", kind="rubric", liturgy_name="Ash Wednesday", section="The Liturgy",
        body_text=body,
    ).endswith("\n\n" + body)
    assert compose_prayer_thanksgiving_embed(
        title="X", genre="prayer", section_header="Sec", body_text=body,
    ).endswith("\n\n" + body)


def test_all_composers_mention_anglican_or_bcp() -> None:
    """Every composed string names its tradition/source so the embedding lands
    in the correct neighborhood."""
    body = "b"
    assert "Anglican" in compose_collect_embed(
        name="X", rite="rite_ii", section="common", body_text=body,
    )
    assert "Anglican" in compose_daily_office_embed(
        name="X", kind="prayer", office="compline", rite="rite_ii", body_text=body,
    )
    assert "Book of Common Prayer" in compose_psalter_embed(
        number=1, title="Psalm 1", latin_incipit=None, body_text=body,
    )
    assert "Anglican" in compose_proper_liturgy_embed(
        name="X", kind="rubric", liturgy_name="Ash Wednesday", section="Ash Wednesday",
        body_text=body,
    )
    assert "Book of Common Prayer" in compose_prayer_thanksgiving_embed(
        title="X", genre="prayer", section_header="Sec", body_text=body,
    )
