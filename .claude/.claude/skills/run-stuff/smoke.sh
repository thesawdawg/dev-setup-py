#!/usr/bin/env bash
# Smoke test for dev-setup CLI. Run from repo root.
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
echo "=== dev-setup smoke tests ==="
echo ""

check "version" uv run dev-setup version
check_output "version number" "1\." uv run dev-setup version
check "list (all)" uv run dev-setup list
check "list core" uv run dev-setup list core
check "list tools" uv run dev-setup list tools
check "list --installed" uv run dev-setup list --installed
check "list --available" uv run dev-setup list --available
check "catalog path" uv run dev-setup catalog path
check_output "catalog path output" ".config/dev-setup" uv run dev-setup catalog path
check "catalog export" uv run dev-setup catalog export /tmp/dev-setup-smoke-export.yaml
check_output "help flag" "Commands:" uv run dev-setup --help

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "All $PASS checks passed."
else
  echo "$FAIL/$((PASS+FAIL)) checks failed."
  exit 1
fi
