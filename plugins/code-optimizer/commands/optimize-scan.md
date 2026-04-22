---
description: Read-only audit of the codebase across 11 optimization categories — produces findings.json + a markdown scan report. Never modifies source files.
argument-hint: [--categories a,b,c] [--paths "src/**"] [--exclude "dist/**"] [--out .optimize]
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
model: sonnet
---

# Optimize: scan

You orchestrate a read-only scan of the project across the 11 optimization categories handled by this plugin. You do NOT detect issues yourself — the `optimization-architect` dispatches specialized scanners in parallel. This command produces a findings report and writes nothing to source files.

## Arguments

`$ARGUMENTS` may contain:

- `--categories <list>` — comma-separated subset of: `deduplication, type-consolidation, dead-code, circular-deps, type-strengthening, error-handling, slop-removal, complexity, magic-constants, naming, excessive-parameters`. Default: all.
- `--paths <glob>` — include-glob relative to repo root. Default: from `.code-optimizer.yml` or `src/**`.
- `--exclude <glob>` — exclude-glob. Default: `**/node_modules/**, **/dist/**, **/build/**, **/__generated__/**, **/.next/**`.
- `--out <dir>` — output directory relative to repo root. Default: `.optimize`.

## Steps

### 1. Resolve configuration

- Determine repo root: `Bash git rev-parse --show-toplevel` (fall back to CWD).
- `Glob` for `.code-optimizer.yml` at the repo root. If found, `Read` it and merge with CLI args (CLI wins).
- Build the final config: `{ repo_root, enabled_categories[], include_paths[], exclude_paths[], out_dir, commands: {lint,typecheck,test,build} }`.
- Create `<out_dir>/`, `<out_dir>/raw/`, `<out_dir>/findings-per-scanner/`.

### 2. Detect the ecosystem

Run:

```
${CLAUDE_PLUGIN_ROOT}/scripts/detect_ecosystem.sh <repo_root> > <out_dir>/ecosystem.json
```

`Read` the output. If no known ecosystem is detected (empty languages array), ask the user via `AskUserQuestion` whether to proceed with language-agnostic semantic scanners only, or abort.

### 3. Run static tools

Run:

```
${CLAUDE_PLUGIN_ROOT}/scripts/run_static_tools.sh <repo_root> <out_dir>
```

This populates `<out_dir>/raw/` with whatever tools are available (knip/madge/jscpd for TS-JS, vulture/radon for Python). Failures are logged but never abort. If zero tools succeed for the detected ecosystem, continue — scanners will fall back to semantic analysis.

### 4. Dispatch the architect

Use the `Task` tool with `subagent_type: optimization-architect`. Pass:

- `repo_root`
- `out_dir`
- `raw_dir`: `<out_dir>/raw`
- `ecosystem_json`: `<out_dir>/ecosystem.json`
- `enabled_categories` (the resolved list)
- `include_paths`, `exclude_paths`
- `config`

The architect will spawn the individual category scanners in parallel and return a summary JSON with `findings_dir` and `completed[]` / `missing[]`.

### 5. Aggregate findings

Run:

```
${CLAUDE_PLUGIN_ROOT}/scripts/parse_findings.py <out_dir>/findings-per-scanner <out_dir>
```

This writes `<out_dir>/findings.json` (flat sorted array) and `<out_dir>/optimize-scan-report.md`.

### 6. Present summary

Print to the user:

```
## Scan complete

Repository: <repo_root>
Ecosystem:  <languages> (<managers>)
Categories: <completed count>/<requested count>
Findings:   <total>  [high: N / medium: N / low: N]
Review:     <N require manual review>

Outputs:
  Report: <out_dir>/optimize-scan-report.md
  JSON:   <out_dir>/findings.json

Next:
  /optimize:plan                     # turn findings into a checklist
  /optimize:apply --category <name>  # once a checklist exists
```

If any scanner failed (appears in `missing[]`), list them with their error summary and suggest re-running just those with `--categories`.

## Never do

- Do NOT edit any source file.
- Do NOT commit, stash, or otherwise touch git state.
- Do NOT install or upgrade any tool on the user's machine.
- Do NOT run the scanners yourself if they fail — surface the failure and let the user decide.

## Idempotency

`/optimize:scan` always overwrites `<out_dir>/findings.json` and the per-scanner files. It preserves `.optimize/baseline.json` and `.optimize/batches/` if they exist from a previous `/optimize:apply` run.
