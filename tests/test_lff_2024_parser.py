"""Tests for the LFF 2024 PDF parser.

All tests run against the pinned fixture PDF:
    tests/fixtures/lff_2024.pdf
    SHA256: 5deea4a131b6c90218ae07b92a17f68bfce000a24a04edd989cdb1edc332bfd7

JSON fixture (pre-computed for fast CI):
    tests/fixtures/lff_2024/commemorations.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commonplace_server.liturgical_parsers.lff_2024 import (
    EXPECTED_SHA256,
    ParsedCommemoration,
    parse_lff_2024,
    verify_pdf_sha256,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_PDF_PATH = _FIXTURE_DIR / "lff_2024.pdf"
_JSON_PATH = _FIXTURE_DIR / "lff_2024" / "commemorations.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture() -> list[ParsedCommemoration]:
    """Load pre-parsed commemorations from JSON fixture."""
    with open(_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    from commonplace_server.liturgical_parsers.lff_2024 import ParsedCollectEntry

    result = []
    for d in data:
        result.append(
            ParsedCommemoration(
                name=d["name"],
                date=d["date"],
                feast_slug=d["feast_slug"],
                canonical_id=d["canonical_id"],
                subtitle=d["subtitle"],
                bio_text=d["bio_text"],
                collects=[
                    ParsedCollectEntry(rite=c["rite"], text=c["text"])
                    for c in d["collects"]
                ],
                lesson_refs=d["lesson_refs"],
                preface=d["preface"],
                trial_use=d["trial_use"],
                page_number=d["page_number"],
                tradition=d["tradition"],
                source=d["source"],
                genre=d["genre"],
                category=d["category"],
                raw_metadata=d["raw_metadata"],
            )
        )
    return result


def _find(comms: list[ParsedCommemoration], name_fragment: str) -> ParsedCommemoration:
    """Return the first commemoration whose name contains name_fragment."""
    for c in comms:
        if name_fragment in c.name:
            return c
    raise AssertionError(
        f"No commemoration with name containing {name_fragment!r}. "
        f"Available names: {[c.name for c in comms[:10]]!r}…"
    )


# ---------------------------------------------------------------------------
# SHA256 guard — must pass before fixture can be trusted
# ---------------------------------------------------------------------------


class TestSHA256Verification:
    """Guard against PDF replacement; must run before any parse-dependent test."""

    def test_pdf_sha256_matches_pin(self) -> None:
        """Verify the fixture PDF matches the pinned SHA256."""
        assert _PDF_PATH.exists(), f"Fixture PDF not found at {_PDF_PATH}"
        assert verify_pdf_sha256(_PDF_PATH), (
            f"SHA256 mismatch! Fixture PDF at {_PDF_PATH} does not match "
            f"pinned hash {EXPECTED_SHA256}. "
            "If the PDF was intentionally replaced, update EXPECTED_SHA256 in "
            "commonplace_server/liturgical_parsers/lff_2024.py "
            "and regenerate tests/fixtures/lff_2024/commemorations.json."
        )

    def test_expected_sha256_constant(self) -> None:
        """Constant in the module matches the known-good value."""
        assert EXPECTED_SHA256 == "5deea4a131b6c90218ae07b92a17f68bfce000a24a04edd989cdb1edc332bfd7"


# ---------------------------------------------------------------------------
# Total count
# ---------------------------------------------------------------------------


class TestTotalCount:
    """Verify total commemoration count is within expected bounds."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_total_count_in_range(self) -> None:
        """Should parse between 150 and 320 commemorations."""
        n = len(self.comms)
        assert 150 <= n <= 320, (
            f"Expected 150–320 commemorations, got {n}. "
            "This may indicate a parser regression or a different edition of the PDF."
        )

    def test_total_count_exact(self) -> None:
        """Exact count as of lff_2024.pdf fixture run."""
        assert len(self.comms) == 283

    def test_all_have_names(self) -> None:
        for c in self.comms:
            assert c.name, f"Empty name on page {c.page_number}"

    def test_all_have_dates(self) -> None:
        for c in self.comms:
            assert c.date, f"Empty date for {c.name!r}"

    def test_all_have_collects(self) -> None:
        for c in self.comms:
            assert c.collects, f"No collects for {c.name!r} on page {c.page_number}"

    def test_all_have_feast_slug(self) -> None:
        for c in self.comms:
            assert c.feast_slug.endswith("_anglican"), (
                f"Slug {c.feast_slug!r} for {c.name!r} does not end with '_anglican'"
            )

    def test_tradition_and_source(self) -> None:
        for c in self.comms:
            assert c.tradition == "anglican"
            assert c.source == "lff_2024"


# ---------------------------------------------------------------------------
# Spot-check named entries
# ---------------------------------------------------------------------------


class TestNamedEntries:
    """Five+ spot-checks on well-known commemorations."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_elizabeth_ann_seton(self) -> None:
        c = _find(self.comms, "Elizabeth Ann Seton")
        assert c.date == "January 4"
        assert "Vowed Religious" in c.subtitle
        assert "1821" in c.subtitle
        assert "Sisters of Charity" in c.bio_text
        assert len(c.collects) == 2
        assert "Give us grace" in c.collects[0].text
        assert c.collects[0].rite == "rite_i"
        assert c.collects[1].rite == "rite_ii"
        assert "Amen." in c.collects[0].text
        assert not c.trial_use
        assert c.feast_slug == "elizabeth_ann_seton_anglican"

    def test_thomas_aquinas(self) -> None:
        c = _find(self.comms, "Thomas Aquinas")
        assert c.date == "January 28"
        assert "Friar and Theologian" in c.subtitle
        assert "Dominican" in c.bio_text
        assert len(c.collects) == 2
        assert "Almighty God" in c.collects[0].text
        assert "enriched" in c.collects[0].text
        assert c.preface == "Preface of Trinity Sunday"
        assert "Wisdom 7:7-14" in c.lesson_refs

    def test_francis_of_assisi(self) -> None:
        c = _find(self.comms, "Francis of Assisi")
        assert c.date == "October 4"
        assert "Friar and Deacon" in c.subtitle
        assert "1226" in c.subtitle
        assert len(c.collects) == 2
        assert "omnipotent" in c.collects[0].text or "Most high" in c.collects[0].text

    def test_sarah_theodora_syncletica(self) -> None:
        c = _find(self.comms, "Sarah, Theodora, and Syncletica")
        assert c.date == "January 5"
        assert "Desert Mothers" in c.subtitle
        assert "Apophthegmata" in c.bio_text  # italicised title in bio
        assert len(c.collects) == 2
        assert "Fix our hearts" in c.collects[0].text

    def test_harriet_bedell(self) -> None:
        c = _find(self.comms, "Harriet Bedell")
        assert c.date == "January 8"
        assert "Deaconess" in c.subtitle
        assert "Buffalo" in c.bio_text
        assert len(c.collects) == 2
        assert "compassion" in c.collects[0].text.lower()

    def test_holy_name(self) -> None:
        c = _find(self.comms, "The Holy Name of Our Lord Jesus Christ")
        assert c.date == "January 1"
        assert not c.subtitle
        assert "Feast of the Holy Name" in c.bio_text
        assert len(c.collects) == 2
        assert "Eternal Father" in c.collects[0].text
        assert c.preface == "Preface of the Incarnation"


# ---------------------------------------------------------------------------
# Boundary detection — two adjacent entries split cleanly
# ---------------------------------------------------------------------------


class TestBoundaryDetection:
    """Verify the state machine correctly splits adjacent entries."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_seton_and_syncletica_are_separate(self) -> None:
        """January 4 (Seton) and January 5 (Syncletica) parse as distinct entries."""
        seton = _find(self.comms, "Elizabeth Ann Seton")
        syncletica = _find(self.comms, "Sarah, Theodora, and Syncletica")
        assert seton.date != syncletica.date
        assert "Sisters of Charity" not in syncletica.bio_text
        assert "Apophthegmata" not in seton.bio_text

    def test_adjacent_january_entries_have_correct_dates(self) -> None:
        jan_names = [
            ("Elizabeth Ann Seton", "January 4"),
            ("Sarah, Theodora, and Syncletica of Egypt", "January 5"),
            ("The Epiphany of Our Lord Jesus Christ", "January 6"),
            ("Harriet Bedell", "January 8"),
        ]
        for name_frag, expected_date in jan_names:
            c = _find(self.comms, name_frag)
            assert c.date == expected_date, (
                f"{name_frag!r}: expected date {expected_date!r}, got {c.date!r}"
            )

    def test_no_duplicate_names(self) -> None:
        """Each canonical slug should be unique."""
        slugs = [c.feast_slug for c in self.comms]
        duplicates = [s for s in set(slugs) if slugs.count(s) > 1]
        assert not duplicates, f"Duplicate feast_slugs: {duplicates}"

    def test_collect_pages_are_adjacent_or_close(self) -> None:
        """Collect page numbers should be monotonically increasing."""
        pages = [c.page_number for c in self.comms if c.page_number is not None]
        for a, b in zip(pages, pages[1:]):
            assert a < b, f"Non-monotonic pages: {a} >= {b}"


# ---------------------------------------------------------------------------
# Trial-use (bracketed) entries
# ---------------------------------------------------------------------------


class TestTrialUseEntries:
    """Commemorations in brackets are trial-use; handle them correctly."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()
        self.trial_use = [c for c in self.comms if c.trial_use]

    def test_trial_use_count(self) -> None:
        """There are exactly 5 trial-use entries in lff_2024.pdf."""
        assert len(self.trial_use) == 5

    def test_liliuokalani_is_trial_use(self) -> None:
        c = _find(self.comms, "Lili")
        assert c.trial_use
        assert c.name.startswith("[")
        assert c.name.endswith("]")
        assert c.date == "January 29"
        assert len(c.collects) == 2

    def test_george_of_lydda_is_trial_use(self) -> None:
        c = _find(self.comms, "George of Lydda")
        assert c.trial_use
        assert c.date == "May 6"

    def test_ordination_philadelphia_eleven_is_trial_use(self) -> None:
        c = _find(self.comms, "Philadelphia Eleven")
        assert c.trial_use
        assert c.date == "July 29"
        assert len(c.collects) == 2

    def test_trial_use_slugs_stripped_of_brackets(self) -> None:
        """Slugs should not contain brackets — they're stripped for slug generation."""
        for c in self.trial_use:
            assert "[" not in c.feast_slug
            assert "]" not in c.feast_slug
            assert c.feast_slug.endswith("_anglican")


# ---------------------------------------------------------------------------
# No-bio edge cases
# ---------------------------------------------------------------------------


class TestNoBioEdgeCases:
    """Entries without bio text (collect-only or special pages)."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_second_proper_christmas_has_no_bio(self) -> None:
        c = _find(self.comms, "A Second Proper for Christmas Day")
        # Second Proper references the Third Proper's lesson set; has no bio
        assert c.bio_text == "" or len(c.bio_text) < 20
        assert len(c.collects) == 2
        assert c.date == "December 25"

    def test_all_no_bio_have_collects(self) -> None:
        """Even entries without biographical notes have collects."""
        no_bio = [c for c in self.comms if not c.bio_text]
        for c in no_bio:
            assert c.collects, f"{c.name!r} has no bio and no collects"


# ---------------------------------------------------------------------------
# Alternate-date / movable feast edge cases
# ---------------------------------------------------------------------------


class TestAlternateDateEntries:
    """Some entries appear under a different calendar date than their usual feast day."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_liliuokalani_has_january_29_date(self) -> None:
        """Lili'uokalani is traditionally November 11 but placed at January 29."""
        c = _find(self.comms, "Lili")
        assert c.date == "January 29"

    def test_ordination_philadelphia_eleven_date(self) -> None:
        """The Ordination of the Philadelphia Eleven is dated July 29."""
        c = _find(self.comms, "Philadelphia Eleven")
        assert c.date == "July 29"

    def test_cornelius_centurion_date(self) -> None:
        """Cornelius the Centurion has an out-of-calendar bottom-of-page date."""
        c = _find(self.comms, "Cornelius the Centurion")
        assert c.date == "October 20"
        assert len(c.bio_text) > 100


# ---------------------------------------------------------------------------
# Collect structure integrity
# ---------------------------------------------------------------------------


class TestCollectStructure:
    """Verify collect text integrity for all parsed entries."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_all_rite_i_collects_start_correctly(self) -> None:
        """Rite I collects should not start with 'I ' (rite marker leaked)."""
        for c in self.comms:
            for col in c.collects:
                if col.rite == "rite_i":
                    assert not col.text.startswith("I "), (
                        f"{c.name!r} Rite I collect starts with 'I ': {col.text[:40]!r}"
                    )

    def test_all_rite_ii_collects_dont_start_with_ii(self) -> None:
        """Rite II collects should not start with 'II' (rite marker leaked)."""
        for c in self.comms:
            for col in c.collects:
                if col.rite == "rite_ii":
                    assert not col.text.startswith("II"), (
                        f"{c.name!r} Rite II collect starts with 'II': {col.text[:40]!r}"
                    )

    def test_most_collects_end_with_amen(self) -> None:
        """Nearly all collects should end with 'Amen.'"""
        missing_amen: list[str] = []
        for c in self.comms:
            for col in c.collects:
                if not col.text.endswith("Amen."):
                    missing_amen.append(f"{c.name!r} ({col.rite})")
        # Allow up to 5 edge cases (special propers)
        assert len(missing_amen) <= 5, (
            f"Too many collects missing 'Amen.' ({len(missing_amen)}): "
            f"{missing_amen[:10]}"
        )

    def test_rite_i_ii_ordering(self) -> None:
        """When both rites present, Rite I comes before Rite II."""
        for c in self.comms:
            if len(c.collects) == 2:
                assert c.collects[0].rite == "rite_i"
                assert c.collects[1].rite == "rite_ii"

    def test_elie_naud_has_both_rites(self) -> None:
        """Élie Naud had a tricky 'I <tab>' format; verify both rites parsed."""
        c = _find(self.comms, "lie Naud")
        assert len(c.collects) == 2
        assert c.collects[0].rite == "rite_i"
        assert c.collects[1].rite == "rite_ii"
        # Rite I should NOT start with rite marker
        assert not c.collects[0].text.startswith("I ")
        assert "Blessed God" in c.collects[0].text


# ---------------------------------------------------------------------------
# Slug integrity
# ---------------------------------------------------------------------------


class TestSlugIntegrity:
    """Verify canonical slug generation matches _make_slug spec."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_slug_ends_with_anglican(self) -> None:
        for c in self.comms:
            assert c.feast_slug.endswith("_anglican")

    def test_slug_only_alnum_and_underscore(self) -> None:
        import re
        for c in self.comms:
            assert re.fullmatch(r"[a-z0-9_]+", c.feast_slug), (
                f"Invalid slug {c.feast_slug!r} for {c.name!r}"
            )

    def test_known_slugs(self) -> None:
        """Spot-check a few canonical slugs."""
        expected = [
            ("Elizabeth Ann Seton", "elizabeth_ann_seton_anglican"),
            ("Thomas Aquinas", "thomas_aquinas_anglican"),
            ("Francis of Assisi", "francis_of_assisi_anglican"),
        ]
        for name_frag, expected_slug in expected:
            c = _find(self.comms, name_frag)
            assert c.feast_slug == expected_slug, (
                f"{name_frag!r}: expected slug {expected_slug!r}, got {c.feast_slug!r}"
            )

    def test_canonical_id_equals_feast_slug(self) -> None:
        for c in self.comms:
            assert c.canonical_id == c.feast_slug


# ---------------------------------------------------------------------------
# Raw metadata integrity
# ---------------------------------------------------------------------------


class TestRawMetadata:
    """Verify raw_metadata JSON is well-formed."""

    def setup_method(self) -> None:
        self.comms = _load_fixture()

    def test_raw_metadata_is_valid_json(self) -> None:
        for c in self.comms:
            meta = json.loads(c.raw_metadata)
            assert "page_number" in meta
            assert "date" in meta
            assert "source" in meta
            assert meta["source"] == "lff_2024"

    def test_raw_metadata_trial_use_flag(self) -> None:
        for c in self.comms:
            meta = json.loads(c.raw_metadata)
            assert meta["trial_use"] == c.trial_use


# ---------------------------------------------------------------------------
# Live parse (slow) — skipped in CI unless RUN_LIVE_PARSE=1
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _PDF_PATH.exists(),
    reason="Fixture PDF not available",
)
class TestLiveParse:
    """Run the actual parser against the PDF (slower than fixture tests)."""

    @pytest.fixture(scope="class")
    def comms(self) -> list[ParsedCommemoration]:
        return parse_lff_2024(_PDF_PATH)

    def test_live_count_matches_fixture(self, comms: list[ParsedCommemoration]) -> None:
        """Live parse should produce same count as fixture."""
        fixture_comms = _load_fixture()
        assert len(comms) == len(fixture_comms)

    def test_live_first_entry(self, comms: list[ParsedCommemoration]) -> None:
        """First commemoration should be The Holy Name of Our Lord Jesus Christ."""
        assert comms[0].name == "The Holy Name of Our Lord Jesus Christ"
        assert comms[0].date == "January 1"

    def test_live_last_entry(self, comms: list[ParsedCommemoration]) -> None:
        """Last commemoration should be Frances Joseph Gaudet."""
        assert comms[-1].name == "Frances Joseph Gaudet"
        assert comms[-1].date == "December 31"
