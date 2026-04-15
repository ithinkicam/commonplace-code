#!/usr/bin/env bash
# Scaffold a new synthesis skill: skills/<name>/SKILL.md
# Usage: make new-skill name=my_skill    (or: bash scripts/new-skill.sh my_skill)

set -euo pipefail

NAME="${1:-}"
if [ -z "$NAME" ]; then
  echo "usage: $0 <skill_name>" >&2
  exit 2
fi

if ! [[ "$NAME" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "skill name must be snake_case, lowercase, starting with a letter" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_DIR="$REPO_ROOT/skills/$NAME"

if [ -e "$SKILL_DIR" ]; then
  echo "skill already exists: $SKILL_DIR" >&2
  exit 1
fi

mkdir -p "$SKILL_DIR"
cat > "$SKILL_DIR/SKILL.md" <<EOF
---
name: $NAME
description: TODO — one line: what this skill does and when the worker invokes it.
model: sonnet
---

# $NAME

TODO: prompt body. Keep under 5K tokens. Lead with the task, then constraints, then output contract.

## Inputs

- \`TODO\`: describe each context field the invoker passes in.

## Output contract

- TODO: describe the exact shape the invoker expects back. Schema, format, required fields, length bounds.

## Do not

- TODO: things this skill should never do.
EOF

echo "created: $SKILL_DIR/SKILL.md"
echo "next: edit the prompt body, commit."
