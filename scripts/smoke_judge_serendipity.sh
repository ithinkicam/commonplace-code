#!/usr/bin/env bash
# Smoke test: run every judge_serendipity fixture through claude -p and validate the output.
# Invocation: claude -p --system-prompt-file <SKILL.md> --model haiku
# Pass = output parses via skills/judge_serendipity/parser.py AND every candidate id
#        from the fixture appears exactly once in accepted/rejected/triangulation,
#        AND the 2-item cap is respected.
# This is a structural/contract smoke, not a quality smoke. Prompt-quality iteration
# lives in task 4.7.
# Exits 0 only if all fixtures pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE_DIR="$REPO_ROOT/skills/judge_serendipity/fixtures"
SKILL_FILE="$REPO_ROOT/skills/judge_serendipity/SKILL.md"
PARSER="$REPO_ROOT/skills/judge_serendipity/parser.py"
CLAUDE="${CLAUDE_BIN:-claude}"
MODEL="${JUDGE_SERENDIPITY_MODEL:-haiku}"
PY="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

PASS=0
FAIL=0
FAILURES=()

echo "=== smoke_judge_serendipity: running fixtures in $FIXTURE_DIR ==="
echo "model: $MODEL"
echo "skill: $SKILL_FILE"
echo ""

for fixture in "$FIXTURE_DIR"/*.json; do
  name="$(basename "$fixture")"
  echo -n "  $name ... "

  # Invoke claude -p with SKILL.md as system prompt and fixture JSON as stdin.
  output="$(cat "$fixture" | "$CLAUDE" -p --system-prompt-file "$SKILL_FILE" --model "$MODEL" 2>&1)" || {
    echo "FAIL (claude invocation error)"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: claude invocation failed")
    continue
  }

  # Preamble guard: the first non-empty character must be '{' OR '`' (the
  # opening of a ```json fence — Haiku's common tic, tolerated here; the parser
  # has a strip_code_fences helper that consumers apply before parse).
  # Anything else (conversational "Here is:", plain prose) is a hard fail.
  first_char="$(printf '%s' "$output" | head -c 1)"
  if [ "$first_char" != "{" ] && [ "$first_char" != '`' ]; then
    echo "FAIL (preamble leak: first char is '$first_char', expected '{' or code fence)"
    echo "    output head: $(printf '%s' "$output" | head -c 160)"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: preamble leak")
    continue
  fi

  # Run the parser + coverage check in Python against the actual fixture.
  result="$(
    FIXTURE="$fixture" OUTPUT="$output" REPO="$REPO_ROOT" "$PY" - <<'PYEOF'
import json
import os
import sys

sys.path.insert(0, os.path.join(os.environ["REPO"], "skills", "judge_serendipity"))

from parser import (  # type: ignore
    ParseError,
    parse,
    strip_code_fences,
    validate_reject_reason_prefix,
)

fixture_path = os.environ["FIXTURE"]
output = os.environ["OUTPUT"]

with open(fixture_path) as f:
    fixture = json.load(f)

expected_ids = [c["id"] for c in fixture["candidates"]]

# Normalize: Haiku tends to wrap JSON in markdown code fences. Strip once.
normalized = strip_code_fences(output)

try:
    judgment = parse(normalized, expected_ids=expected_ids)
except ParseError as e:
    print(f"PARSE_ERROR: {e}")
    sys.exit(2)

# Advisory: count how many reject reasons use approved prefixes.
total_rejected = len(judgment.rejected)
unapproved = sum(
    1 for r in judgment.rejected if not validate_reject_reason_prefix(r.reason)
)
advisory = ""
if total_rejected and unapproved:
    advisory = f" [advisory: {unapproved}/{total_rejected} reject reasons lack approved prefix]"

print(
    f"OK (accepted={len(judgment.accepted)},"
    f" rejected={len(judgment.rejected)},"
    f" triangulation_groups={len(judgment.triangulation_groups)},"
    f" surfaced={judgment.surfaced_count()}/2){advisory}"
)
PYEOF
  )" || {
    echo "FAIL"
    echo "    $result" | sed 's/^/    /'
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: $(echo "$result" | head -1)")
    continue
  }

  echo "PASS $(echo "$result" | tail -1)"
  PASS=$((PASS + 1))
done

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "${#FAILURES[@]}" -gt 0 ]; then
  echo ""
  echo "Failures:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi

exit 0
