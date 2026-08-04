#!/usr/bin/env bash
# Smoke test for devstuff CLI. Run from repo root.
set -euo pipefail

PASS=0
FAIL=0

check() {
  local label="$1"; shift
  if output=$("$@" 2>&1); then
    echo "  ✔  $label"
    PASS=$((PASS+1))
  else
    echo "  ✘  $label"
    echo "     Output: $output"
    FAIL=$((FAIL+1))
  fi
}

check_output() {
  local label="$1"; local expected="$2"; shift 2
  output=$("$@" 2>&1)
  if echo "$output" | grep -q "$expected"; then
    echo "  ✔  $label"
    PASS=$((PASS+1))
  else
    echo "  ✘  $label (expected '$expected' in output)"
    echo "     Got: $output"
    FAIL=$((FAIL+1))
  fi
}

echo ""
echo "=== devstuff smoke tests ==="
echo ""

check "version" uv run devstuff version
check_output "version number" "1\." uv run devstuff version
check "list (all)" uv run devstuff list
check "list core" uv run devstuff list core
check "list tools" uv run devstuff list tools
check "list --installed" uv run devstuff list --installed
check "list --available" uv run devstuff list --available
check "catalog path" uv run devstuff catalog path
check_output "catalog path output" ".config/devstuff" uv run devstuff catalog path
check "catalog export" uv run devstuff catalog export /tmp/devstuff-smoke-export.yaml
check_output "help flag" "Commands:" uv run devstuff --help

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "All $PASS checks passed."
else
  echo "$FAIL/$((PASS+FAIL)) checks failed."
  exit 1
fi
