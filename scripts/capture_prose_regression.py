#!/usr/bin/env python3
"""One-shot script to capture the prose regression baseline.

Runs the current surface pipeline on 20 seeds and writes
tests/fixtures/prose_regression.json.

Usage:
    python scripts/capture_prose_regression.py [--out PATH] [--timeout SECS]

Requires:
    - Ollama running with nomic-embed-text model
    - claude CLI available in PATH
    - COMMONPLACE_DB_PATH or ~/commonplace/library.db present with vectors

WARNING: This script calls the live judge (claude -p haiku) for each seed.
It takes several minutes. Run it once and commit the output.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

# Add repo root to path so this script is runnable from any working directory.
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.WARNING)

from commonplace_server import surface as surface_mod  # noqa: E402 (after sys.path insert)
from commonplace_server.surface import run_surface  # noqa: E402

CAPTURE_JUDGE_TIMEOUT = 120  # seconds

# ---------------------------------------------------------------------------
# Seeds: 10 prose/accept-path + 10 technical/reject-path
# None are liturgical — purpose is to pin pre-4.3 behavior on pure prose.
# ---------------------------------------------------------------------------

SEEDS = [
    # --- Prose seeds: should exercise the ACCEPT path ---
    {
        "id": "seed_01",
        "kind": "synthetic",
        "theme": "grief",
        "content": (
            "I am thinking about grief as something that transforms rather than resolves. "
            "Judith Butler writes about tarrying with grief rather than moving through it — "
            "as if grief is itself a mode of attention to what we have lost and to what we "
            "share with others who have lost."
        ),
    },
    {
        "id": "seed_02",
        "kind": "synthetic",
        "theme": "mercy",
        "content": (
            "What does it mean to show mercy to someone who has not asked for it? "
            "Not forgiveness, exactly — more like a decision to carry the weight of another "
            "person's failure without demanding they acknowledge it."
        ),
    },
    {
        "id": "seed_03",
        "kind": "synthetic",
        "theme": "attention",
        "content": (
            "Simone Weil describes attention as the highest form of generosity — a way of "
            "making yourself empty so that another person can fill the space. I wonder if "
            "prayer and good listening are the same act."
        ),
    },
    {
        "id": "seed_04",
        "kind": "synthetic",
        "theme": "community",
        "content": (
            "A community is not simply a collection of individuals who happen to share a "
            "place or a purpose. It is constituted by shared practices, mutual obligations, "
            "and a common story that gives the individual's life meaning within a larger whole."
        ),
    },
    {
        "id": "seed_05",
        "kind": "synthetic",
        "theme": "failure",
        "content": (
            "Failure is morally instructive in a way that success is not. When things go wrong, "
            "you are forced to reckon with the gap between what you intended and what you "
            "produced — and that reckoning can be the beginning of wisdom."
        ),
    },
    {
        "id": "seed_06",
        "kind": "synthetic",
        "theme": "love",
        "content": (
            "There is a difference between love that wants to possess and love that wants the "
            "other person to flourish. The first kind reduces the beloved to an object of need; "
            "the second is willing to be eclipsed by the beloved's good."
        ),
    },
    {
        "id": "seed_07",
        "kind": "synthetic",
        "theme": "death",
        "content": (
            "How do you prepare for your own death? Not practically — legally, financially — "
            "but spiritually, existentially. The Christian tradition has practices for this: "
            "the ars moriendi, memento mori. But most of us live as if we will not die."
        ),
    },
    {
        "id": "seed_08",
        "kind": "synthetic",
        "theme": "craft",
        "content": (
            "Writing is a kind of thinking, not just a record of thought. You do not know what "
            "you think until you have tried to articulate it — and the attempt to articulate "
            "often reveals that what you thought you thought was wrong."
        ),
    },
    {
        "id": "seed_09",
        "kind": "synthetic",
        "theme": "technology",
        "content": (
            "The question is not whether technology changes us — of course it does — but how "
            "it changes the texture of our attention, our capacity for depth, our relationship "
            "to slowness and difficulty. Speed and friction are not neutral."
        ),
    },
    {
        "id": "seed_10",
        "kind": "synthetic",
        "theme": "work",
        "content": (
            "There is a tradition that sees work as vocation — a calling that is irreducible "
            "to its economic function. You do not work merely to produce goods or earn wages; "
            "you work because the activity itself is a form of meaning-making."
        ),
    },
    # --- Technical seeds: should exercise the REJECT path ---
    {
        "id": "seed_11",
        "kind": "synthetic",
        "theme": "API design (technical)",
        "content": (
            "The endpoint should return a 422 Unprocessable Entity when required fields are "
            "missing from the request body. We should distinguish between 400 Bad Request "
            "(malformed JSON) and 422 (structurally valid but semantically incomplete)."
        ),
    },
    {
        "id": "seed_12",
        "kind": "synthetic",
        "theme": "code review (technical)",
        "content": (
            "This function is doing too many things. Extract the database query into its own "
            "method, add a docstring, and fix the off-by-one in the pagination logic. Also "
            "the variable name `res` should be more descriptive."
        ),
    },
    {
        "id": "seed_13",
        "kind": "synthetic",
        "theme": "meeting minutes (technical)",
        "content": (
            "Action items from today's standup: (1) Alice will fix the flaky test in CI by "
            "EOD Thursday. (2) Bob will open a PR for the new logging format. (3) Carol to "
            "schedule a design review for the auth refactor."
        ),
    },
    {
        "id": "seed_14",
        "kind": "synthetic",
        "theme": "error message (technical)",
        "content": (
            "TypeError: Cannot read properties of undefined (reading 'map'). Stack trace: "
            "at renderList (App.jsx:42), at App (App.jsx:18). The component is trying to "
            "map over a prop that is undefined on initial render."
        ),
    },
    {
        "id": "seed_15",
        "kind": "synthetic",
        "theme": "how-to content (technical)",
        "content": (
            "To install the package, run `npm install --save-dev eslint`. Then create a "
            "`.eslintrc.json` file in your project root. Add the rules you want to enforce, "
            "and run `npx eslint .` to lint your files."
        ),
    },
    {
        "id": "seed_16",
        "kind": "synthetic",
        "theme": "deployment notes (technical)",
        "content": (
            "The deploy to production requires incrementing the database migration version, "
            "running `make migrate`, and then restarting the server. Blue-green deployment "
            "is not yet set up, so expect 30-60 seconds of downtime."
        ),
    },
    {
        "id": "seed_17",
        "kind": "synthetic",
        "theme": "performance metrics (technical)",
        "content": (
            "P95 latency on the search endpoint is 340ms, up from 180ms last week. The spike "
            "correlates with the increase in candidate pool size after the re-indexing job. "
            "We may need to add an index on the `created_at` column."
        ),
    },
    {
        "id": "seed_18",
        "kind": "synthetic",
        "theme": "dependency management (technical)",
        "content": (
            "Dependabot has flagged 14 outdated packages. The critical one is lodash < 4.17.21 "
            "due to CVE-2021-23337. The others are minor version bumps that should be safe to "
            "merge. Review the lockfile diff before approving."
        ),
    },
    {
        "id": "seed_19",
        "kind": "synthetic",
        "theme": "SQL query optimization (technical)",
        "content": (
            "The query is doing a full table scan because the WHERE clause uses LIKE '%term%' "
            "with a leading wildcard. Consider switching to full-text search with FTS5 or "
            "adding a trigram index if partial matching is required."
        ),
    },
    {
        "id": "seed_20",
        "kind": "synthetic",
        "theme": "changelog entry (technical)",
        "content": (
            "v2.3.1 — Fixed a race condition in the job queue that caused duplicate processing "
            "under high load. Added retry logic with exponential backoff. Updated dependencies: "
            "httpx 0.27 -> 0.28, pydantic 2.5 -> 2.6."
        ),
    },
]


def capture_seed(seed_def: dict) -> dict:
    """Run surface pipeline for one seed and return structured result."""
    result = run_surface(
        seed=seed_def["content"],
        mode="ambient",
        # similarity_floor=0.0 bypasses the broken similarity calculation
        # (stored vectors are not unit-normalized, so all computed similarities
        # are 0.0; the default floor of 0.55 would drop everything).
        # See pipeline quirks note in prose_regression.json.
        similarity_floor=0.0,
        limit=10,
    )

    candidate_pool: list[dict] = []
    judge_verdicts: list[dict] = []

    for item in result.get("accepted", []):
        candidate_pool.append(
            {
                "candidate_id": item["id"],
                "source_type": item["source_type"],
                "source_title": item["source_title"],
                "source_uri": item.get("source_uri", ""),
                "similarity_score": item["similarity_score"],
                "chunk_text_snippet": item["text"][:300],
            }
        )
        judge_verdicts.append(
            {
                "candidate_id": item["id"],
                "accept": True,
                "verdict_type": "accepted",
                "reason": item.get("reason", ""),
            }
        )

    for group in result.get("triangulation_groups", []):
        for item in group.get("items", []):
            candidate_pool.append(
                {
                    "candidate_id": item["id"],
                    "source_type": item["source_type"],
                    "source_title": item["source_title"],
                    "source_uri": item.get("source_uri", ""),
                    "similarity_score": item["similarity_score"],
                    "chunk_text_snippet": item["text"][:300],
                }
            )
            judge_verdicts.append(
                {
                    "candidate_id": item["id"],
                    "accept": True,
                    "verdict_type": "triangulation",
                    "group_reason": group.get("reason", ""),
                }
            )

    rejected_count = result.get("rejected_count", 0)
    pipeline_note = result.get("note")

    # Infer "all rejected" implicit verdicts when we know rejected_count but
    # have no individual rejected records (run_surface doesn't surface them).
    # The fixture records the count; the regression test checks accept/reject
    # per surfaced candidate, so this is sufficient.
    return {
        "id": seed_def["id"],
        "kind": seed_def["kind"],
        "theme": seed_def["theme"],
        "content": seed_def["content"],
        "candidate_pool": candidate_pool,
        "judge_verdicts": judge_verdicts,
        "rejected_count": rejected_count,
        "pipeline_note": pipeline_note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "tests" / "fixtures" / "prose_regression.json"),
        help="Output fixture path",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=CAPTURE_JUDGE_TIMEOUT,
        help="Judge timeout in seconds (default: 120)",
    )
    args = parser.parse_args()

    surface_mod.JUDGE_TIMEOUT = args.timeout

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    import subprocess

    try:
        skill_md_sha = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", "skills/judge_serendipity/SKILL.md"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except Exception:
        skill_md_sha = "unknown"

    print(f"Capturing baseline for {len(SEEDS)} seeds...", flush=True)
    print(f"Judge timeout: {args.timeout}s per seed", flush=True)
    print(f"Output: {out_path}", flush=True)
    print(f"SKILL.md git SHA: {skill_md_sha}", flush=True)
    print(flush=True)

    seed_results = []
    for i, seed_def in enumerate(SEEDS):
        print(
            f"[{i + 1}/{len(SEEDS)}] {seed_def['id']} ({seed_def['theme']})...",
            end="",
            flush=True,
        )
        entry = capture_seed(seed_def)
        accepted_count = len(entry["candidate_pool"])
        print(
            f" accepted={accepted_count}, rejected={entry['rejected_count']}, "
            f"note={entry['pipeline_note']}",
            flush=True,
        )
        seed_results.append(entry)

    # Summary stats
    total_accepted = sum(len(s["candidate_pool"]) for s in seed_results)
    total_rejected = sum(s["rejected_count"] for s in seed_results)
    seeds_with_pipeline_failure = [
        s["id"] for s in seed_results if s["pipeline_note"] is not None
    ]

    fixture = {
        "captured_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "pipeline_version_note": (
            f"pre-4.3 judge rubric; skills/judge_serendipity/SKILL.md @ {skill_md_sha}. "
            "similarity_floor=0.0 (live corpus vectors not unit-normalized; "
            "default floor=0.55 would drop all candidates — see pipeline_quirks)."
        ),
        "pipeline_quirks": (
            "Stored chunk_vectors have magnitude ~19-23 (not unit-normalized). "
            "_distance_to_similarity(distance) = max(0, 1-distance) always returns 0.0 "
            "for L2 distances > 1. Default similarity_floor=0.55 drops every candidate. "
            "Baseline captured with similarity_floor=0.0 so the judge is actually invoked. "
            "This is a pre-existing bug unrelated to task 4.3."
        ),
        "capture_stats": {
            "total_seeds": len(seed_results),
            "prose_seeds": 10,
            "technical_seeds": 10,
            "total_accepted": total_accepted,
            "total_rejected": total_rejected,
            "seeds_with_pipeline_failure": seeds_with_pipeline_failure,
        },
        "seeds": seed_results,
    }

    out_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False))
    print(f"\nWrote fixture to {out_path}")
    print(
        f"Stats: {total_accepted} accepted, {total_rejected} rejected "
        f"across {len(seed_results)} seeds."
    )
    if seeds_with_pipeline_failure:
        print(
            f"WARNING: {len(seeds_with_pipeline_failure)} seeds had pipeline failures "
            f"(judge timeout/parse error): {seeds_with_pipeline_failure}"
        )


if __name__ == "__main__":
    main()
