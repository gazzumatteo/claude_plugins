#!/usr/bin/env bash
# run_baseline.sh — capture the regression baseline for a project.
#
# Usage:
#   run_baseline.sh <repo-root> <out-dir> [--lint CMD] [--typecheck CMD] [--test CMD] [--build CMD]
#
# Writes:
#   <out-dir>/baseline.json      — machine-readable summary
#   <out-dir>/baseline.lint.log  — raw outputs (for diff later)
#   <out-dir>/baseline.typecheck.log
#   <out-dir>/baseline.test.log
#   <out-dir>/baseline.build.log
#
# Each command is optional — skipped (marked "skipped") if not provided.
# Exit code is ALWAYS 0 so the orchestrator can inspect the JSON even when the
# project's own commands fail (that IS the baseline).

set -u
set -o pipefail

repo="${1:-}"
out="${2:-}"
if [[ -z "$repo" || -z "$out" ]]; then
  echo "usage: run_baseline.sh <repo> <out-dir> [--lint CMD] [--typecheck CMD] [--test CMD] [--build CMD]" >&2
  exit 2
fi
shift 2

lint_cmd=""; typecheck_cmd=""; test_cmd=""; build_cmd=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --lint)      lint_cmd="$2"; shift 2 ;;
    --typecheck) typecheck_cmd="$2"; shift 2 ;;
    --test)      test_cmd="$2"; shift 2 ;;
    --build)     build_cmd="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$out"
cd "$repo"

run_phase() {
  local name="$1" cmd="$2"
  local log="$out/baseline.$name.log"
  if [[ -z "$cmd" ]]; then
    echo "{\"phase\":\"$name\",\"status\":\"skipped\"}"
    return
  fi
  local start end exit_code
  start=$(date +%s)
  bash -lc "$cmd" >"$log" 2>&1
  exit_code=$?
  end=$(date +%s)
  local hash
  if command -v sha256sum >/dev/null 2>&1; then
    hash=$(sha256sum "$log" 2>/dev/null | awk '{print $1}')
  else
    hash=$(shasum -a 256 "$log" 2>/dev/null | awk '{print $1}')
  fi
  # try to pull sensible counts per phase
  # NOTE: grep -c returns exit 1 on zero matches AND prints nothing (not "0") on
  # binary-contaminated logs. We force text mode with -a so ANSI escapes / null
  # bytes from test runners don't silently zero out the count. `|| true` plus
  # `tail -n1` + `${n:-0}` fallback guarantee a single integer token under
  # `set -o pipefail`.
  count_matches() {
    local n
    n=$({ grep -acE "$1" "$2" 2>/dev/null || true; } | tail -n1)
    echo "${n:-0}"
  }
  local passed=0 failed=0 errors=0
  case "$name" in
    lint)
      errors=$(count_matches "(error|✖)" "$log") ;;
    typecheck)
      errors=$(count_matches "(error TS[0-9]+|: error:)" "$log") ;;
    test)
      passed=$(count_matches "([0-9]+) (passed|passing)" "$log")
      failed=$(count_matches "([0-9]+) (failed|failing)" "$log") ;;
    build)
      errors=$(count_matches "(error|failed)" "$log") ;;
  esac
  echo "{\"phase\":\"$name\",\"status\":\"$( [[ $exit_code -eq 0 ]] && echo ok || echo failed )\",\"exit_code\":$exit_code,\"duration_s\":$((end - start)),\"log_sha256\":\"$hash\",\"log_path\":\"$log\",\"counts\":{\"passed\":$passed,\"failed\":$failed,\"errors\":$errors}}"
}

# run all phases, collect JSON fragments
lint_json=$(run_phase lint "$lint_cmd")
typecheck_json=$(run_phase typecheck "$typecheck_cmd")
test_json=$(run_phase test "$test_cmd")
build_json=$(run_phase build "$build_cmd")

commit_sha=$(git -C "$repo" rev-parse --verify HEAD 2>/dev/null || true)
[[ -z "$commit_sha" ]] && commit_sha="unknown"
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat >"$out/baseline.json" <<EOF
{
  "repo": "$repo",
  "commit": "$commit_sha",
  "timestamp": "$timestamp",
  "phases": {
    "lint":      $lint_json,
    "typecheck": $typecheck_json,
    "test":      $test_json,
    "build":     $build_json
  }
}
EOF

echo "$out/baseline.json"
