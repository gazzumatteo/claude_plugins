#!/usr/bin/env bash
# detect_ecosystem.sh — classify the target repo's languages and available tools.
#
# Usage:
#   detect_ecosystem.sh <repo-root>
#
# Emits JSON on stdout:
#   {
#     "languages": ["ts", "js", "python", "go", "rust"],
#     "managers":  ["npm", "pnpm", "yarn", "uv", "poetry", "pip", "go", "cargo"],
#     "configs":   {"tsconfig": "tsconfig.json", "pyproject": "pyproject.toml", ...},
#     "tools_available": {"knip": true, "madge": true, "jscpd": true, "vulture": false, "radon": false},
#     "commands_detected": {"lint": "npm run lint" | null, "typecheck": ..., "test": ..., "build": ...}
#   }
#
# Notes:
# - Read-only.
# - Shells out to `jq` if available; falls back to plain echo-JSON otherwise.
# - Never installs anything; only reports what IS present.

set -u
set -o pipefail

repo="${1:-}"
if [[ -z "$repo" || ! -d "$repo" ]]; then
  echo "usage: detect_ecosystem.sh <repo-root>" >&2
  exit 2
fi

cd "$repo"

has_file() { [[ -e "$1" ]] && echo true || echo false; }
has_cmd()  { command -v "$1" >/dev/null 2>&1 && echo true || echo false; }

# ---------------------------------------------------------------- languages
langs=()
[[ -f "package.json" ]] && langs+=("js")
[[ -f "tsconfig.json" || -n "$(find . -maxdepth 3 -name 'tsconfig*.json' -print -quit 2>/dev/null)" ]] && langs+=("ts")
{ [[ -f "pyproject.toml" ]] || [[ -f "requirements.txt" ]] || [[ -f "setup.py" ]] || [[ -f "Pipfile" ]]; } && langs+=("python")
[[ -f "go.mod" ]] && langs+=("go")
[[ -f "Cargo.toml" ]] && langs+=("rust")
[[ -f "pubspec.yaml" ]] && langs+=("dart")
[[ -f "composer.json" ]] && langs+=("php")
[[ -f "Gemfile" ]] && langs+=("ruby")

# deduplicate
langs_dedup=()
for l in "${langs[@]:-}"; do
  dup=false
  for d in "${langs_dedup[@]:-}"; do [[ "$d" == "$l" ]] && dup=true && break; done
  $dup || langs_dedup+=("$l")
done

# ---------------------------------------------------------------- managers
managers=()
if [[ -f "package.json" ]]; then
  if [[ -f "pnpm-lock.yaml" ]]; then managers+=("pnpm")
  elif [[ -f "yarn.lock" ]]; then managers+=("yarn")
  elif [[ -f "bun.lockb" ]]; then managers+=("bun")
  else managers+=("npm"); fi
fi
if [[ -f "pyproject.toml" ]]; then
  if grep -q '^\[tool\.poetry\]' pyproject.toml 2>/dev/null; then managers+=("poetry")
  elif [[ -f "uv.lock" ]] || grep -q '^\[tool\.uv\]' pyproject.toml 2>/dev/null; then managers+=("uv")
  else managers+=("pip"); fi
elif [[ -f "requirements.txt" ]]; then managers+=("pip"); fi
[[ -f "go.mod" ]] && managers+=("go")
[[ -f "Cargo.toml" ]] && managers+=("cargo")
[[ -f "Gemfile" ]] && managers+=("bundler")

# ---------------------------------------------------------------- tool availability
# A tool is "available" if either it is on PATH, or it is declared in the project
# (devDependencies for npm, pyproject for python, etc.) — in the latter case the plugin
# will call it via the local runner (npx / uv run / poetry run).

is_npm_dep() {
  local name="$1"
  [[ -f "package.json" ]] || return 1
  grep -q "\"$name\"\\s*:" package.json
}

is_py_dep() {
  local name="$1"
  { [[ -f "pyproject.toml" ]] && grep -q "$name" pyproject.toml; } \
    || { [[ -f "requirements.txt" ]] && grep -q -i "^$name\b" requirements.txt; } \
    || return 1
}

tool_available() {
  # $1 = binary name, $2 = npm package name (optional), $3 = python package name (optional)
  local bin="$1" npm_pkg="${2:-}" py_pkg="${3:-}"
  if command -v "$bin" >/dev/null 2>&1; then echo true; return; fi
  if [[ -n "$npm_pkg" ]] && is_npm_dep "$npm_pkg"; then echo true; return; fi
  if [[ -n "$py_pkg" ]] && is_py_dep "$py_pkg"; then echo true; return; fi
  echo false
}

knip=$(tool_available knip knip)
madge=$(tool_available madge madge)
jscpd=$(tool_available jscpd jscpd)
eslint=$(tool_available eslint eslint)
tsc=$(tool_available tsc typescript)
vulture=$(tool_available vulture "" vulture)
radon=$(tool_available radon "" radon)
ruff=$(tool_available ruff "" ruff)
pyright=$(tool_available pyright pyright pyright)
mypy=$(tool_available mypy "" mypy)
pytest=$(tool_available pytest "" pytest)

# ---------------------------------------------------------------- commands
# Peek at package.json scripts / Makefile / pyproject scripts to guess the project's
# own lint/typecheck/test/build commands. Never guaranteed — the user can override in
# .code-optimizer.yml.

lint_cmd="null"
typecheck_cmd="null"
test_cmd="null"
build_cmd="null"

if [[ -f "package.json" ]]; then
  # scripts inspection — match on "key": to avoid partial hits like "lintfix" / "prebuild"
  if   grep -qE '"lint"\s*:' package.json; then lint_cmd='"npm run lint"'; fi
  if   grep -qE '"typecheck"\s*:'  package.json; then typecheck_cmd='"npm run typecheck"';
  elif grep -qE '"type-check"\s*:' package.json; then typecheck_cmd='"npm run type-check"';
  elif [[ "$tsc" == "true" ]]; then                  typecheck_cmd='"npx tsc --noEmit"'; fi
  if   grep -qE '"test"\s*:'  package.json; then test_cmd='"npm test"'; fi
  if   grep -qE '"build"\s*:' package.json; then build_cmd='"npm run build"'; fi
fi

if [[ -f "pyproject.toml" || -f "requirements.txt" ]]; then
  if [[ "$ruff" == "true" && "$lint_cmd" == "null" ]]; then lint_cmd='"ruff check ."'; fi
  if [[ "$pyright" == "true" && "$typecheck_cmd" == "null" ]]; then typecheck_cmd='"pyright"';
  elif [[ "$mypy" == "true" && "$typecheck_cmd" == "null" ]]; then typecheck_cmd='"mypy ."'; fi
  if [[ "$pytest" == "true" && "$test_cmd" == "null" ]]; then test_cmd='"pytest"'; fi
fi

if [[ -f "go.mod" ]]; then
  [[ "$lint_cmd" == "null" ]] && lint_cmd='"go vet ./..."'
  [[ "$test_cmd" == "null" ]] && test_cmd='"go test ./..."'
  [[ "$build_cmd" == "null" ]] && build_cmd='"go build ./..."'
fi

# ---------------------------------------------------------------- emit JSON
# manual JSON to avoid jq dependency
join_arr() {
  local sep=", " out=""
  local n=0
  for x in "$@"; do
    [[ -z "$x" ]] && continue
    out+="\"$x\"$sep"
    n=$((n + 1))
  done
  [[ $n -eq 0 ]] && return 0
  echo "${out%$sep}"
}

cat <<EOF
{
  "languages": [$(join_arr "${langs_dedup[@]:-}")],
  "managers":  [$(join_arr "${managers[@]:-}")],
  "configs": {
    "tsconfig":   $(has_file tsconfig.json),
    "pyproject":  $(has_file pyproject.toml),
    "package":    $(has_file package.json),
    "gomod":      $(has_file go.mod),
    "cargo":      $(has_file Cargo.toml)
  },
  "tools_available": {
    "knip":    $knip,
    "madge":   $madge,
    "jscpd":   $jscpd,
    "eslint":  $eslint,
    "tsc":     $tsc,
    "vulture": $vulture,
    "radon":   $radon,
    "ruff":    $ruff,
    "pyright": $pyright,
    "mypy":    $mypy,
    "pytest":  $pytest
  },
  "commands_detected": {
    "lint":      $lint_cmd,
    "typecheck": $typecheck_cmd,
    "test":      $test_cmd,
    "build":     $build_cmd
  }
}
EOF
