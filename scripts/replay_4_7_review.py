#!/usr/bin/env python3
"""Task 4.7 replay harness: run prose regression + liturgical fixtures live.

Produces a single JSON results artifact at
``build/4_7_replay_results.json`` for the primary to inspect when
applying the Moderate bar per docs/liturgical-ingest-plan.md §6 Q4.

This is a one-shot review tool. It is NOT in the normal pytest suite.
Structural integrity of both fixtures is covered by:
- ``tests/test_prose_regression_offline.py``
- ``tests/test_liturgical_surfacing_offline.py``

The replay here invokes the live judge (claude -p haiku) and the live
Ollama embedding stack against the actual library.db. Expect ~75s per
seed × 40 seeds = ~50 minutes wall clock.

Usage::

    .venv/bin/python scripts/replay_4_7_review.py [--out PATH] [--timeout SECS]

Per task 4.2 forward flag (b): liturgical-fixture assertions match loosely
on kind + feast/title substring, not slug-exact.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.WARNING)

# Must import after path munging.
from commonplace_server import surface as surface_mod  # noqa: E402
from commonplace_server.surface import run_surface  # noqa: E402

DEFAULT_JUDGE_TIMEOUT = 180

PROSE_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "prose_regression.json"
LITURGICAL_FIXTURE_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "liturgical_surfacing.json"
)


_MATCHER_STOPWORDS = frozenset({"a", "an", "the", "of", "for", "to", "and"})
_MATCHER_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_LFF_RITE_SUFFIX_RE = re.compile(r"_rite-(i|ii|iii)$")


def _normalize_tokens(text: str) -> set[str]:
    """Lowercase, strip punctuation, drop stopwords, collapse numeric leading zeros.

    Used on both slugs (``ash_wednesday_the_imposition_of_ashes``) and display
    titles (``Optional Block (Ash Wednesday)``) so their token sets can be
    compared for overlap. Leading-zero numerics collapse so ``psalm_023``
    matches a title like "Psalm 23".
    """
    cleaned = _MATCHER_PUNCT_RE.sub(" ", text.lower())
    out: set[str] = set()
    for tok in cleaned.split():
        if not tok or tok in _MATCHER_STOPWORDS:
            continue
        if tok.isdigit():
            tok = str(int(tok))
        out.add(tok)
    return out


def _expected_slug_tokens(expected: dict) -> set[str]:
    """Content tokens from an expected_surface slug, minus tradition/rite suffixes."""
    sid = expected["source_id"]
    for suffix in ("_anglican", "_orthodox", "_catholic"):
        if sid.endswith(suffix):
            sid = sid[: -len(suffix)]
            break
    sid = _LFF_RITE_SUFFIX_RE.sub("", sid)
    return _normalize_tokens(sid)


def _kind_matches(hit: dict, expected_kind: str) -> bool:
    """Does this hit satisfy an expected_surface entry's ``kind``?

    Two fixture/DB impedance mismatches to bridge:

    - Fixture uses dashes for compound kinds (``prayer-body``) while
      ``liturgy_bcp.py`` ingest normalizes to underscores via
      ``unit.kind.replace('-', '_')`` before writing ``liturgical_unit_meta.genre``.
    - Fixture expects ``kind='bio'`` for LFF commemorations, but
      ``liturgy_lff.py`` writes bios as ``content_type='prose'`` with no
      ``liturgical_unit_meta`` row (so ``genre`` is absent).
    """
    source_type = str(hit.get("source_type", ""))
    if expected_kind == "bio":
        return source_type == "prose"
    if source_type != "liturgical_unit":
        return False
    expected_norm = expected_kind.replace("-", "_")
    hit_norm = (hit.get("genre") or "").replace("-", "_")
    return hit_norm == expected_norm


def _title_token_overlap_match(source_title: str, slug_tokens: set[str]) -> bool:
    """Loose name match: slug tokens ∩ title tokens ≥ min(2, len(slug_tokens)).

    Short slugs (1–2 tokens) require full overlap to avoid false positives
    (e.g., ``proper_21`` shouldn't match a "Proper 15" collect). Longer slugs
    need ≥2 content-word overlap, so the actual display title
    ("Optional Block (Ash Wednesday)") still credits against a long slug
    (``ash_wednesday_the_imposition_of_ashes``).
    """
    if not slug_tokens:
        return False
    overlap = slug_tokens & _normalize_tokens(source_title)
    return len(overlap) >= min(2, len(slug_tokens))


def _match_expected_pairs(
    expected_surface: list[dict], current_accepted: list[dict]
) -> list[dict]:
    """For a positive liturgical case, return credited (expected, hit) pairs.

    Each expected pair credits at most once (first matching hit wins).
    Iterates ``current_accepted`` (not just liturgical hits) so bio
    expectations can match their prose documents.
    """
    matched: list[dict] = []
    for exp in expected_surface:
        slug_tokens = _expected_slug_tokens(exp)
        for hit in current_accepted:
            if not _kind_matches(hit, exp["kind"]):
                continue
            if not _title_token_overlap_match(hit["source_title"], slug_tokens):
                continue
            matched.append(
                {
                    "expected_source_id": exp["source_id"],
                    "expected_kind": exp["kind"],
                    "matched_hit": hit["candidate_id"],
                    "matched_title": hit["source_title"],
                }
            )
            break
    return matched


def replay_prose_seed(seed: dict, judge_timeout: int) -> dict:
    """Re-run the surface pipeline for one prose seed."""
    surface_mod.JUDGE_TIMEOUT = judge_timeout

    t0 = time.time()
    try:
        result = run_surface(
            seed=seed["content"],
            mode="ambient",
            similarity_floor=0.0,
            limit=10,
        )
        err = None
    except Exception as exc:
        result = {"accepted": [], "triangulation_groups": [], "note": f"replay error: {exc}"}
        err = str(exc)

    elapsed = round(time.time() - t0, 2)

    current_accepted: list[dict] = []
    for item in result.get("accepted", []):
        current_accepted.append(
            {
                "candidate_id": item["id"],
                "source_type": item.get("source_type", ""),
                "source_title": item.get("source_title", ""),
                "verdict_type": "accepted",
                "reason": item.get("reason", ""),
                "frame": item.get("frame"),  # present for liturgical
            }
        )
    for group in result.get("triangulation_groups", []):
        for item in group["items"]:
            current_accepted.append(
                {
                    "candidate_id": item["id"],
                    "source_type": item.get("source_type", ""),
                    "source_title": item.get("source_title", ""),
                    "verdict_type": "triangulation",
                    "group_reason": group.get("reason", ""),
                    "frame": item.get("frame"),
                }
            )

    baseline_ids = {v["candidate_id"] for v in seed.get("judge_verdicts", [])}
    current_ids = {c["candidate_id"] for c in current_accepted}

    flipped_to_reject = sorted(baseline_ids - current_ids)
    new_accepts = sorted(current_ids - baseline_ids)
    stayed = sorted(baseline_ids & current_ids)

    # Liturgy-spillover detection (block criterion per §6 Q4).
    spillover = [
        c
        for c in current_accepted
        if c["source_type"] == "liturgical_unit"
    ]

    return {
        "seed_id": seed["id"],
        "seed_theme": seed["theme"],
        "elapsed_seconds": elapsed,
        "baseline_accept_ids": sorted(baseline_ids),
        "current_accept": current_accepted,
        "flipped_to_reject": flipped_to_reject,
        "new_accepts": new_accepts,
        "stayed_accepted": stayed,
        "spillover_liturgical": spillover,
        "rejected_count": result.get("rejected_count"),
        "note": result.get("note"),
        "error": err,
    }


def replay_liturgical_case(case: dict, judge_timeout: int) -> dict:
    """Replay one liturgical case and report pass/fail against expectations.

    Pass semantics:
    - ``positive``: at least one accepted candidate has source_type==liturgical_unit
      AND matches one of the expected entries on (kind, title substring).
    - ``negative_true``: no accepted candidates at all (nothing surfaces).
    - ``negative_spillover``: no accepted candidates of source_type==liturgical_unit
      (prose candidates are fine).
    """
    surface_mod.JUDGE_TIMEOUT = judge_timeout

    t0 = time.time()
    try:
        result = run_surface(
            seed=case["seed"],
            mode="ambient",
            similarity_floor=0.0,
            limit=10,
        )
        err = None
    except Exception as exc:
        result = {"accepted": [], "triangulation_groups": [], "note": f"replay error: {exc}"}
        err = str(exc)
    elapsed = round(time.time() - t0, 2)

    current_accepted: list[dict] = []
    for item in result.get("accepted", []):
        current_accepted.append(
            {
                "candidate_id": item["id"],
                "source_type": item.get("source_type", ""),
                "source_title": item.get("source_title", ""),
                "verdict_type": "accepted",
                "reason": item.get("reason", ""),
                "frame": item.get("frame"),
                "category": item.get("category"),
                "genre": item.get("genre"),
                "feast_name": item.get("feast_name"),
                "tradition": item.get("tradition"),
            }
        )
    for group in result.get("triangulation_groups", []):
        for item in group["items"]:
            current_accepted.append(
                {
                    "candidate_id": item["id"],
                    "source_type": item.get("source_type", ""),
                    "source_title": item.get("source_title", ""),
                    "verdict_type": "triangulation",
                    "group_reason": group.get("reason", ""),
                    "frame": item.get("frame"),
                    "category": item.get("category"),
                    "genre": item.get("genre"),
                    "feast_name": item.get("feast_name"),
                    "tradition": item.get("tradition"),
                }
            )

    liturgical_hits = [
        c for c in current_accepted if c["source_type"] == "liturgical_unit"
    ]
    prose_hits = [
        c for c in current_accepted if c["source_type"] != "liturgical_unit"
    ]

    category = case["category"]
    passed = False
    mismatch_reason = None

    if category == "positive":
        # Positive: at least one hit matching an expected entry on kind + token
        # overlap (slug exactness relaxed per 4.2 flag b). Iterates all
        # accepted hits so ``kind='bio'`` expectations can match prose docs.
        matched_expectations = _match_expected_pairs(
            case["expected_surface"], current_accepted
        )
        if matched_expectations:
            passed = True
        else:
            passed = False
            mismatch_reason = (
                "no candidate matched any expected (kind, name) pair; "
                f"liturgical_hits={[h['source_title'] for h in liturgical_hits]}, "
                f"prose_hits={[h['source_title'][:40] for h in prose_hits]}"
            )
        return {
            "case_id": case["id"],
            "category": category,
            "theme": case["theme"],
            "elapsed_seconds": elapsed,
            "accepted": current_accepted,
            "liturgical_hit_count": len(liturgical_hits),
            "prose_hit_count": len(prose_hits),
            "matched_expectations": matched_expectations,
            "passed": passed,
            "mismatch_reason": mismatch_reason,
            "rejected_count": result.get("rejected_count"),
            "note": result.get("note"),
            "error": err,
        }

    if category == "negative_true":
        # No surfacings at all.
        if not current_accepted:
            passed = True
        else:
            passed = False
            mismatch_reason = (
                f"expected 0 accepts; got {len(current_accepted)} "
                f"({[c['source_title'][:40] for c in current_accepted]})"
            )
        return {
            "case_id": case["id"],
            "category": category,
            "theme": case["theme"],
            "elapsed_seconds": elapsed,
            "accepted": current_accepted,
            "liturgical_hit_count": len(liturgical_hits),
            "prose_hit_count": len(prose_hits),
            "passed": passed,
            "mismatch_reason": mismatch_reason,
            "rejected_count": result.get("rejected_count"),
            "note": result.get("note"),
            "error": err,
        }

    # category == "negative_spillover"
    if not liturgical_hits:
        passed = True
    else:
        passed = False
        mismatch_reason = (
            f"spillover trap triggered — {len(liturgical_hits)} liturgical "
            f"hit(s) for a prose-register seed: "
            f"{[(h['source_title'][:40], h.get('genre')) for h in liturgical_hits]}"
        )
    return {
        "case_id": case["id"],
        "category": category,
        "theme": case["theme"],
        "elapsed_seconds": elapsed,
        "accepted": current_accepted,
        "liturgical_hit_count": len(liturgical_hits),
        "prose_hit_count": len(prose_hits),
        "passed": passed,
        "mismatch_reason": mismatch_reason,
        "rejected_count": result.get("rejected_count"),
        "note": result.get("note"),
        "error": err,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "build" / "4_7_replay_results.json"),
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_JUDGE_TIMEOUT)
    parser.add_argument(
        "--prose-only",
        action="store_true",
        help="Run only prose regression seeds",
    )
    parser.add_argument(
        "--liturgical-only",
        action="store_true",
        help="Run only liturgical fixture cases",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(PROSE_FIXTURE_PATH) as f:
        prose_fixture = json.load(f)
    with open(LITURGICAL_FIXTURE_PATH) as f:
        liturgical_fixture = json.load(f)

    prose_results: list[dict] = []
    liturgical_results: list[dict] = []

    if not args.liturgical_only:
        seeds = prose_fixture["seeds"]
        for i, seed in enumerate(seeds):
            print(
                f"[PROSE {i + 1}/{len(seeds)}] {seed['id']} ({seed['theme']})...",
                flush=True,
            )
            entry = replay_prose_seed(seed, args.timeout)
            prose_results.append(entry)
            print(
                f"  elapsed={entry['elapsed_seconds']}s, "
                f"baseline_accept={len(entry['baseline_accept_ids'])}, "
                f"now_accept={len(entry['current_accept'])}, "
                f"flipped_to_reject={len(entry['flipped_to_reject'])}, "
                f"new_accepts={len(entry['new_accepts'])}, "
                f"spillover={len(entry['spillover_liturgical'])}",
                flush=True,
            )

    if not args.prose_only:
        cases = liturgical_fixture["cases"]
        for i, case in enumerate(cases):
            print(
                f"[LIT {i + 1}/{len(cases)}] {case['id']} ({case['category']} / {case['theme']})...",
                flush=True,
            )
            entry = replay_liturgical_case(case, args.timeout)
            liturgical_results.append(entry)
            print(
                f"  elapsed={entry['elapsed_seconds']}s, "
                f"lit_hits={entry['liturgical_hit_count']}, "
                f"prose_hits={entry['prose_hit_count']}, "
                f"passed={entry['passed']}"
                + (f", mismatch={entry['mismatch_reason'][:80]}" if entry["mismatch_reason"] else ""),
                flush=True,
            )

    # Summary counters.
    total_flips = sum(
        len(p["flipped_to_reject"]) + len(p["new_accepts"]) for p in prose_results
    )
    total_spillover = sum(len(p["spillover_liturgical"]) for p in prose_results)
    lit_pass = sum(1 for c in liturgical_results if c["passed"])
    lit_total = len(liturgical_results)

    artifact = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "judge_timeout_seconds": args.timeout,
        "prose_baseline_commit": "f420d8e",
        "judge_skill_md_head": "HEAD (includes 4.3 edits)",
        "summary": {
            "prose_total_seeds": len(prose_results),
            "prose_total_flips_or_new_accepts": total_flips,
            "prose_liturgy_spillovers": total_spillover,
            "liturgical_total_cases": lit_total,
            "liturgical_passed": lit_pass,
            "liturgical_failed": lit_total - lit_pass,
        },
        "prose_results": prose_results,
        "liturgical_results": liturgical_results,
    }

    out_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
    print(f"\nWrote results to {out_path}")
    print(
        f"Prose: {total_flips} flip(s)/new-accept(s) across {len(prose_results)} seeds; "
        f"{total_spillover} liturgical spillover(s)."
    )
    print(f"Liturgical: {lit_pass}/{lit_total} passed.")


if __name__ == "__main__":
    main()
