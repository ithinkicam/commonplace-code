#!/usr/bin/env bash
# Smoke test — end-to-end health check. Real assertions get added as features land.
# At Phase 0.0 this is a stub that checks the expected scaffolding exists.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

pass() {
  echo "PASS: $1"
}

check_file() {
  [ -f "$REPO_ROOT/$1" ] || fail "missing file: $1"
  pass "$1 exists"
}

check_dir() {
  [ -d "$REPO_ROOT/$1" ] || fail "missing dir: $1"
  pass "$1/ exists"
}

echo "== Phase 0.0 smoke test =="

check_file "AGENTS.md"
check_file "README.md"
check_file "Makefile"
check_file "pyproject.toml"
check_file "scripts/safe-mode.sh"
check_file "scripts/new-skill.sh"
check_file "docs/plan.md"
check_file "docs/execution-plan.md"
check_file "docs/phase-0-0.md"
check_file "docs/decisions/0001-execution-model.md"
check_file "build/STATE.md.template"
check_file "build/state.json.template"
check_file ".github/workflows/ci.yml"

check_dir "commonplace_server"
check_dir "commonplace_worker"
check_dir "skills"
check_dir "tests"

echo "== smoke test passed =="
