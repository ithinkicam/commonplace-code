"""Prose regression test — guards against accept/reject flips after judge rubric edits.

## Purpose

This test freezes the pre-4.3 judge behavior on 20 prose seeds. After task 4.3
edits skills/judge_serendipity/SKILL.md to add a "Liturgical candidates" section,
re-running this test will surface any changed rulings.

Per docs/liturgical-ingest-plan.md §6 Q4:
  - Score drift is OK (not asserted here).
  - Any accept/reject flip triggers review in task 4.7.
  - "Liturgy-spillover flips" (a pure-prose seed now surfaces a liturgical candidate)
    are a blocking issue.

## Markers

This test is marked @pytest.mark.live because it calls:
  - Ollama (nomic-embed-text) for seed embeddings
  - claude CLI (haiku) for judge verdicts
  - ~/commonplace/library.db for the live corpus

Run with: pytest -m live tests/test_prose_regression.py

Normal `pytest` runs skip this test. The offline structural companion
(test_prose_regression_offline.py) runs always.

## On similarity_floor=0.0

The baseline was captured with similarity_floor=0.0. Stored chunk_vectors have
magnitude ~19-23 (not unit-normalized), so _distance_to_similarity() always
returns 0.0 for L2 distances > 1. The default floor of 0.55 would silently drop
every candidate. This is a pre-existing pipeline bug (tracked separately).
The regression test uses the same floor so comparisons are apples-to-apples.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip the entire module unless -m live is explicitly passed.
# This keeps normal `pytest` runs fast and avoids requiring Ollama + claude CLI.
pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "prose_regression.json"

# Extend judge timeout for test run (live calls can be slow)
_JUDGE_TIMEOUT_SECS = 120


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    assert FIXTURE_PATH.exists(), (
        f"Fixture not found at {FIXTURE_PATH}. "
        "Run: python scripts/capture_prose_regression.py"
    )
    with open(FIXTURE_PATH) as f:
        return json.load(f)


@pytest.mark.live
class TestProseRegression:
    """Re-run the pipeline on each seed and assert no accept/reject flips.

    Scores may drift; only the accept/reject boolean per surfaced candidate
    is asserted. This test PASSES on the initial baseline run and is intended
    to FAIL if task 4.3 changes the judge's accept/reject for any pure-prose seed.
    """

    def _get_surface_fn(self) -> object:
        """Import run_surface and patch judge timeout."""
        from commonplace_server import surface as surface_mod

        surface_mod.JUDGE_TIMEOUT = _JUDGE_TIMEOUT_SECS
        from commonplace_server.surface import run_surface

        return run_surface

    def _run_seed(self, run_surface: object, seed_content: str) -> set[tuple[str, bool]]:
        """Run surface pipeline for one seed and return (candidate_id, accept) pairs."""
        result = run_surface(  # type: ignore[operator]
            seed=seed_content,
            mode="ambient",
            # Must match the floor used at baseline capture time.
            similarity_floor=0.0,
            limit=10,
        )

        verdicts: set[tuple[str, bool]] = set()

        for item in result.get("accepted", []):
            verdicts.add((item["id"], True))

        for group in result.get("triangulation_groups", []):
            for item in group.get("items", []):
                verdicts.add((item["id"], True))

        return verdicts

    def test_no_accept_reject_flips(self, fixture_data: dict) -> None:
        """Assert that accept/reject per candidate matches the frozen baseline.

        Flips are collected and reported together so the 4.7 reviewer gets
        the full picture in one run.
        """
        run_surface = self._get_surface_fn()
        seeds = fixture_data["seeds"]

        all_flips: list[str] = []
        all_new_accepts: list[str] = []

        for seed in seeds:
            seed_id = seed["id"]
            baseline_verdicts: set[tuple[str, bool]] = {
                (v["candidate_id"], v["accept"]) for v in seed.get("judge_verdicts", [])
            }
            pipeline_note = seed.get("pipeline_note")

            # Skip seeds that had pipeline failures at capture time — we cannot
            # compare against a missing baseline. Flag them as a warning.
            if pipeline_note is not None:
                # Pipeline failure at capture time: no baseline to compare.
                # This is expected if judge had a parse failure during capture.
                continue

            # Re-run the pipeline.
            current_verdicts = self._run_seed(run_surface, seed["content"])

            # Find flips: baseline said accept=True but current says absent (reject)
            baseline_accepted = {cid for cid, acc in baseline_verdicts if acc}
            current_accepted = {cid for cid, acc in current_verdicts if acc}

            for cid in baseline_accepted - current_accepted:
                all_flips.append(
                    f"[FLIP accept→reject] {seed_id} / {cid}"
                )

            # Find new accepts: not in baseline at all, now accepted.
            for cid in current_accepted - baseline_accepted:
                all_new_accepts.append(
                    f"[NEW accept] {seed_id} / {cid}"
                )

        # New accepts are suspicious — they may be liturgy-spillover (§6 Q4 block criterion)
        all_issues = all_flips + all_new_accepts

        if all_issues:
            issue_lines = "\n  ".join(all_issues)
            pytest.fail(
                f"Accept/reject flips detected ({len(all_flips)} flips, "
                f"{len(all_new_accepts)} new accepts). "
                f"Each must be reviewed in task 4.7:\n  {issue_lines}"
            )

    def test_no_liturgical_spillover(self, fixture_data: dict) -> None:
        """Assert that no pure-prose seed surfaces a liturgical candidate.

        This is the 'liturgy-spillover' block criterion from §6 Q4. After task
        4.3 adds a Liturgical candidates section to the judge rubric, the judge
        might start accepting liturgical units for prose seeds. That is a bug.
        """
        run_surface = self._get_surface_fn()
        seeds = fixture_data["seeds"]

        spillovers: list[str] = []

        for seed in seeds:
            if seed.get("pipeline_note") is not None:
                continue

            result = run_surface(  # type: ignore[operator]
                seed=seed["content"],
                mode="ambient",
                similarity_floor=0.0,
                limit=10,
            )

            for item in result.get("accepted", []):
                if item.get("source_type") == "liturgical_unit":
                    spillovers.append(
                        f"[LITURGY-SPILLOVER] {seed['id']} / {item['id']} "
                        f"({item.get('source_title', '')[:60]})"
                    )

            for group in result.get("triangulation_groups", []):
                for item in group.get("items", []):
                    if item.get("source_type") == "liturgical_unit":
                        spillovers.append(
                            f"[LITURGY-SPILLOVER in group] {seed['id']} / {item['id']} "
                            f"({item.get('source_title', '')[:60]})"
                        )

        if spillovers:
            lines = "\n  ".join(spillovers)
            pytest.fail(
                f"Liturgical candidates surfaced for pure-prose seeds ({len(spillovers)} cases). "
                f"These are blocking per §6 Q4:\n  {lines}"
            )
