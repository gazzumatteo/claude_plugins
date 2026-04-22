#!/usr/bin/env bash
# run_static_tools.sh — orchestrate the static-analysis tools that feed scanners.
#
# Usage:
#   run_static_tools.sh <repo-root> <out-dir>
#
# Reads the ecosystem info (call detect_ecosystem.sh first) and runs each available
# tool, writing raw output under <out-dir>/raw/ for scanners to consume.
#
# Tools and their outputs:
#   knip      → <out>/raw/knip.json       (unused files/exports/deps, TS/JS)
#   madge     → <out>/raw/madge.json      (dependency graph, circular, TS/JS)
#   jscpd     → <out>/raw/jscpd.json      (duplicate code blocks)
#   eslint    → <out>/raw/eslint.json     (code smells via eslint-plugin-sonarjs if configured)
#   vulture   → <out>/raw/vulture.txt     (dead code, Python)
#   radon     → <out>/raw/radon.json      (complexity, Python)
#
# Every tool is best-effort. Failures are captured in <out>/raw/errors.log — they
# do NOT abort the run. Scanners that depend on a missing tool should fall back to
# Claude-based semantic analysis.

set -u
set -o pipefail

repo="${1:-}"
out="${2:-}"
if [[ -z "$repo" || -z "$out" ]]; then
  echo "usage: run_static_tools.sh <repo> <out-dir>" >&2
  exit 2
fi

mkdir -p "$out/raw"
errlog="$out/raw/errors.log"
: > "$errlog"

cd "$repo"

# helper: run a command, redirect to file, log errors but never abort
try() {
  local label="$1" outfile="$2" cmd="$3"
  echo "--- [$label] $cmd" >>"$errlog"
  if ! bash -lc "$cmd" >"$outfile" 2>>"$errlog"; then
    echo "!!! [$label] failed (exit $?) — output at $outfile" >>"$errlog"
  fi
}

# Prefer project-local binaries; fall back to npx/uvx; final fallback is PATH.
resolve_npm() {
  local bin="$1"
  if [[ -x "node_modules/.bin/$bin" ]]; then
    echo "node_modules/.bin/$bin"
  elif command -v "$bin" >/dev/null 2>&1; then
    echo "$bin"
  else
    echo "npx --yes $bin"
  fi
}

resolve_py() {
  local bin="$1"
  if command -v "$bin" >/dev/null 2>&1; then
    echo "$bin"
  elif command -v uv >/dev/null 2>&1; then
    echo "uv run $bin"
  else
    echo "$bin"  # let it fail cleanly
  fi
}

# ---------------------------------------------------------------- TS/JS tools
if [[ -f "package.json" ]]; then
  KNIP=$(resolve_npm knip)
  MADGE=$(resolve_npm madge)
  JSCPD=$(resolve_npm jscpd)

  try knip   "$out/raw/knip.json"   "$KNIP --reporter json --no-gitignore || true"
  try madge  "$out/raw/madge.json"  "$MADGE --circular --json --extensions js,jsx,ts,tsx,mjs,cjs . || true"
  try jscpd  "$out/raw/jscpd.json"  "$JSCPD --reporters json --output \"$out/raw/jscpd-tmp\" --silent . && cp \"$out/raw/jscpd-tmp/jscpd-report.json\" \"$out/raw/jscpd.json\" || true"
fi

# ---------------------------------------------------------------- Python tools
if [[ -f "pyproject.toml" || -f "requirements.txt" || -f "setup.py" ]]; then
  VULTURE=$(resolve_py vulture)
  RADON=$(resolve_py radon)
  try vulture "$out/raw/vulture.txt" "$VULTURE . --min-confidence 70 || true"
  try radon   "$out/raw/radon.json"  "$RADON cc -j . || true"
fi

# ---------------------------------------------------------------- summary
cat <<EOF
{
  "out_dir": "$out/raw",
  "files": $(ls -1 "$out/raw" 2>/dev/null | awk 'BEGIN{printf "["} {printf "%s\"%s\"", (NR>1?",":""), $0} END{print "]"}')
}
EOF
