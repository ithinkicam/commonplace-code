#!/usr/bin/env bash
# Smoke test: run every summarize_capture fixture through claude -p and validate the output.
# Invocation: claude -p --system-prompt-file <SKILL.md> --model haiku
# Pass = output parses via skills/summarize_capture/parser.py AND every quote is a
#        verbatim substring of the fixture's text.
# Fail = anything else.
# Exits 0 only if all fixtures pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE_DIR="$REPO_ROOT/skills/summarize_capture/fixtures"
SKILL_FILE="$REPO_ROOT/skills/summarize_capture/SKILL.md"
PARSER="$REPO_ROOT/skills/summarize_capture/parser.py"
CLAUDE="${CLAUDE_BIN:-claude}"
MODEL="${SUMMARIZE_CAPTURE_MODEL:-haiku}"
PY="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

PASS=0
FAIL=0
FAILURES=()

echo "=== smoke_summarize_capture: running fixtures in $FIXTURE_DIR ==="
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

  # Preamble guard: first non-empty character must be '-' (start of '---' frontmatter).
  first_char="$(printf '%s' "$output" | head -c 1)"
  if [ "$first_char" != "-" ]; then
    echo "FAIL (preamble leak: first char is '$first_char', expected '-')"
    echo "    output head: $(printf '%s' "$output" | head -c 120)"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: preamble leak")
    continue
  fi

  # Run the parser + quote verification in Python against the actual fixture text.
  # The parser does all the structural validation; verify_quotes handles fabrication.
  result="$(
    FIXTURE="$fixture" OUTPUT="$output" "$PY" - <<'PYEOF'
import json
import os
import sys

sys.path.insert(0, os.path.join(os.environ["PWD"], "skills", "summarize_capture"))

from parser import ParseError, parse, verify_quotes  # type: ignore

fixture_path = os.environ["FIXTURE"]
output = os.environ["OUTPUT"]

with open(fixture_path) as f:
    fixture = json.load(f)

source_text = fixture["text"]

try:
    summary = parse(output)
except ParseError as e:
    print(f"PARSE_ERROR: {e}")
    sys.exit(2)

if summary.too_short:
    print(f"TOO_SHORT_UNEXPECTED: fixture word_count={len(source_text.split())}")
    sys.exit(3)

missing = verify_quotes(summary, source_text)
if missing:
    print("FABRICATED_QUOTES:")
    for q in missing:
        print(f"  - {q[:120]}")
    sys.exit(4)

print(
    f"OK (desc_sentences~={summary.description.count('.')},"
    f" bullets={len(summary.key_points)},"
    f" quotes={len(summary.quotes)})"
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
