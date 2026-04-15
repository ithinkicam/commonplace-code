#!/usr/bin/env bash
# Smoke test: run every classify_book fixture through claude -p and validate the output.
# Invocation: claude -p --system-prompt-file <SKILL.md> --model haiku
# Pass = valid JSON, tier in {HIGH,MEDIUM,LOW}, template in {argument,narrative,poetry}.
# Fail = anything else.
# Exits 0 only if all fixtures pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE_DIR="$REPO_ROOT/skills/classify_book/fixtures"
SKILL_FILE="$REPO_ROOT/skills/classify_book/SKILL.md"
CLAUDE="${CLAUDE_BIN:-claude}"
MODEL="${CLASSIFY_MODEL:-haiku}"

PASS=0
FAIL=0
FAILURES=()

VALID_TIERS=("HIGH" "MEDIUM" "LOW")
VALID_TEMPLATES=("argument" "narrative" "poetry")

contains() {
  local needle="$1"; shift
  for val in "$@"; do
    [[ "$val" == "$needle" ]] && return 0
  done
  return 1
}

echo "=== smoke_classify_book: running fixtures in $FIXTURE_DIR ==="
echo "model: $MODEL"
echo "skill: $SKILL_FILE"
echo ""

for fixture in "$FIXTURE_DIR"/*.json; do
  name="$(basename "$fixture")"
  echo -n "  $name ... "

  # Invoke claude -p with SKILL.md as system prompt and fixture JSON as stdin
  output="$(cat "$fixture" | "$CLAUDE" -p --system-prompt-file "$SKILL_FILE" --model "$MODEL" 2>&1)" || {
    echo "FAIL (claude invocation error)"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: claude invocation failed")
    continue
  }

  # Strip leading/trailing whitespace and find the JSON line
  # The skill may sometimes wrap output in code fences; strip those and find the object
  json_line="$(echo "$output" | tr -d '\r' | sed 's/^```json$//' | sed 's/^```$//' | grep -E '^\{' | head -1 || true)"

  if [ -z "$json_line" ]; then
    echo "FAIL (no JSON line in output)"
    echo "    output was: $output"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: no JSON line in output")
    continue
  fi

  # Validate JSON parseable
  if ! echo "$json_line" | jq . > /dev/null 2>&1; then
    echo "FAIL (invalid JSON)"
    echo "    output was: $json_line"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: invalid JSON")
    continue
  fi

  tier="$(echo "$json_line" | jq -r '.tier // empty')"
  template="$(echo "$json_line" | jq -r '.template // empty')"
  reasoning="$(echo "$json_line" | jq -r '.reasoning // empty')"

  ok=1

  if [ -z "$tier" ]; then
    echo "FAIL (missing tier)"
    ok=0
  elif ! contains "$tier" "${VALID_TIERS[@]}"; then
    echo "FAIL (invalid tier: '$tier')"
    ok=0
  fi

  if [ -z "$template" ]; then
    echo "FAIL (missing template)"
    ok=0
  elif ! contains "$template" "${VALID_TEMPLATES[@]}"; then
    echo "FAIL (invalid template: '$template')"
    ok=0
  fi

  if [ -z "$reasoning" ]; then
    echo "FAIL (missing reasoning)"
    ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    echo "PASS (tier=$tier, template=$template)"
    PASS=$((PASS + 1))
  else
    echo "    json: $json_line"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name: bad tier or template")
  fi
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
