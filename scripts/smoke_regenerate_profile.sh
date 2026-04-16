#!/usr/bin/env bash
# Smoke test: run every regenerate_profile fixture through claude -p and validate the output.
# Invocation: claude -p --system-prompt-file <SKILL.md> --model opus
# Pass = output parses via skills/regenerate_profile/parser.py AND every
#        [directive, YYYY-MM-DD] line from the fixture's current_profile appears
#        byte-for-byte in the output.
# Fail = anything else.
# Exits 0 only if all fixtures pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE_DIR="$REPO_ROOT/skills/regenerate_profile/fixtures"
SKILL_FILE="$REPO_ROOT/skills/regenerate_profile/SKILL.md"
CLAUDE="${CLAUDE_BIN:-claude}"
MODEL="${REGENERATE_PROFILE_MODEL:-opus}"
PY="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

PASS=0
FAIL=0
FAILURES=()

echo "=== smoke_regenerate_profile: running fixtures in $FIXTURE_DIR ==="
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

  # Preamble guard: first character must be '#' (start of the H1).
  first_char="$(printf '%s' "$output" | head -c 1)"
  if [ "$first_char" != "#" ]; then
    echo "FAIL (preamble leak: first char is '$first_char', expected '#')"
    echo "    output head: $(printf '%s' "$output" | head -c 120)"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: preamble leak")
    continue
  fi

  # Run the parser + directive-preservation check in Python against the fixture.
  result="$(
    FIXTURE="$fixture" OUTPUT="$output" REPO_ROOT="$REPO_ROOT" "$PY" - <<'PYEOF'
import json
import os
import sys

repo_root = os.environ["REPO_ROOT"]
sys.path.insert(0, os.path.join(repo_root, "skills", "regenerate_profile"))

from parser import (  # type: ignore
    ParseError,
    extract_directives,
    parse,
    verify_directives_preserved,
)

fixture_path = os.environ["FIXTURE"]
output = os.environ["OUTPUT"]

with open(fixture_path) as f:
    fixture = json.load(f)

input_profile = fixture.get("current_profile", "") or ""

try:
    profile = parse(output)
except ParseError as e:
    print(f"PARSE_ERROR: {e}")
    sys.exit(2)

missing = verify_directives_preserved(input_profile, output)
if missing:
    print("DIRECTIVES_DROPPED:")
    for d in missing:
        print(f"  - {d}")
    sys.exit(3)

input_directive_count = len(extract_directives(input_profile))
print(
    f"OK (sections={len(profile.sections)},"
    f" items={len(profile.all_items())},"
    f" directives_in={input_directive_count},"
    f" directives_out={len(profile.directives())},"
    f" tokens~={profile.token_count_estimate})"
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
