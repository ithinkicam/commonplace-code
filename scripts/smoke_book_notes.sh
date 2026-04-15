#!/usr/bin/env bash
# Smoke test: run each book note fixture through its respective skill and validate output.
# Invocation: claude -p --system-prompt-file <SKILL.md> --model haiku
# Pass = starts with "# " H1, contains all required section headers, non-empty after H1.
# Fail = anything else.
# Exits 0 only if all three smokes pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE="${CLAUDE_BIN:-claude}"
MODEL="${BOOK_NOTE_MODEL:-haiku}"

PASS=0
FAIL=0
FAILURES=()

# Each entry: "skill_name|fixture_file|h1_suffix|required_headers..."
# We'll encode per-skill data in parallel arrays.

SKILLS=("book_note_argument" "book_note_narrative" "book_note_poetry")
FIXTURES=(
  "skills/book_note_argument/fixtures/marcus_aurelius.json"
  "skills/book_note_narrative/fixtures/austen_pride.json"
  "skills/book_note_poetry/fixtures/dickinson.json"
)
H1_SUFFIXES=("argument note" "narrative note" "poetry note")

# Required headers per skill (pipe-separated)
ARGUMENT_HEADERS="## Thesis|## Core argument|## Key moves|## Objections and limits|## Durable takeaways"
NARRATIVE_HEADERS="## Arc|## Voice and texture|## Characters or figures|## Images and scenes|## What it turns on|## Durable takeaways"
POETRY_HEADERS="## Project|## Form and prosody|## Recurring images|## Quiet center|## Durable takeaways"

REQUIRED_HEADERS=("$ARGUMENT_HEADERS" "$NARRATIVE_HEADERS" "$POETRY_HEADERS")

echo "=== smoke_book_notes: running three book note skill fixtures ==="
echo "model: $MODEL"
echo ""

for i in 0 1 2; do
  skill="${SKILLS[$i]}"
  fixture="$REPO_ROOT/${FIXTURES[$i]}"
  skill_file="$REPO_ROOT/skills/$skill/SKILL.md"
  h1_suffix="${H1_SUFFIXES[$i]}"
  headers="${REQUIRED_HEADERS[$i]}"

  echo -n "  $skill ... "

  # Invoke claude -p with SKILL.md as system prompt and fixture JSON as stdin
  output="$(cat "$fixture" | "$CLAUDE" -p --system-prompt-file "$skill_file" --model "$MODEL" 2>&1)" || {
    echo "FAIL (claude invocation error)"
    FAIL=$((FAIL + 1))
    FAILURES+=("$skill: claude invocation failed")
    continue
  }

  ok=1

  # Check starts with "# " H1
  first_line="$(echo "$output" | head -1)"
  if ! echo "$first_line" | grep -qE "^# .+"; then
    echo "FAIL (does not start with H1)"
    echo "    first line: $first_line"
    ok=0
  fi

  # Check H1 contains the expected suffix
  if ! echo "$first_line" | grep -qi "$h1_suffix"; then
    echo "FAIL (H1 missing expected suffix: '$h1_suffix')"
    echo "    first line: $first_line"
    ok=0
  fi

  # Check non-empty after H1 (at least 50 chars of content)
  body="$(echo "$output" | tail -n +2)"
  body_len="${#body}"
  if [ "$body_len" -lt 50 ]; then
    echo "FAIL (body after H1 is too short: ${body_len} chars)"
    ok=0
  fi

  # Check all required section headers are present
  IFS='|' read -ra header_list <<< "$headers"
  for header in "${header_list[@]}"; do
    if ! echo "$output" | grep -qF "$header"; then
      if [ "$ok" -eq 1 ]; then
        echo "FAIL (missing header: '$header')"
        ok=0
      else
        echo "    also missing: '$header'"
      fi
    fi
  done

  if [ "$ok" -eq 1 ]; then
    echo "PASS"
    PASS=$((PASS + 1))
  else
    echo "    --- output preview (first 20 lines) ---"
    echo "$output" | head -20 | sed 's/^/    /'
    echo "    --- end preview ---"
    FAIL=$((FAIL + 1))
    FAILURES+=("$skill: output validation failed")
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
