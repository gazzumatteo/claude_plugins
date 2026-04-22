---
description: Final verification after an apply session. Re-runs lint/typecheck/test/build and compares against the recorded baseline. Produces verify-report.md with verdict and suggests remaining categories.
argument-hint: [--out <dir>]
allowed-tools: Read, Write, Bash, Glob, Task
model: sonnet
---

# Optimize: verify

You produce the final regression-vs-baseline verdict for a completed (or paused) optimization session. No source code changes.

## Arguments

- `--out <dir>` — default `.optimize`.

## Steps

### 1. Locate session state

- `Glob` for `<out_dir>/baseline.json` and `<out_dir>/checklist.md`. Both must exist. Otherwise stop and instruct: run `/optimize:apply` first (or `/optimize:scan` if nothing at all).
- `Read` the baseline and the checklist.

### 2. Summarize the checklist state

Count per status: `[ ]` (pending), `[x]` (done), `[~]` (blocked / regression), `[-]` (skipped).

### 3. Run the final guard

Use `Task` with `subagent_type: regression-guard`:

- `repo_root`
- `out_dir`
- `baseline_path`: `<out_dir>/baseline.json`
- `commands`: same as used for baseline (`Read` `.code-optimizer.yml` or fall back to ecosystem defaults)
- `batch_id`: `final-verify-<timestamp>`

Expect a verdict JSON. Retain it — you'll embed it in the report.

### 4. Write the verify report

Write `<out_dir>/verify-report.md`:

```markdown
# Code optimization verify report

Generated: <ISO8601>
Repo:      <path>
Baseline:  <out_dir>/baseline.json
Checklist: <out_dir>/checklist.md

## Checklist state

| Status      | Count |
|---|---|
| Done        | N |
| Pending     | N |
| Blocked     | N |
| Skipped     | N |

## Final regression verdict: <OK / REGRESSION>

<if regression>
### Phases with regressions
- lint: <details>
- typecheck: <details>
...

### Recommended actions
- List per-batch snapshot refs: `git for-each-ref refs/code-optimizer/`
- Roll back to the pre-batch state of a specific batch: `git reset --hard refs/code-optimizer/batch-<id>` (then remove files listed under that batch's `created_files` in `apply-session.json`)
- Or cherry-pick only the verified batches: see `apply-session.json`
</if>

## Remaining categories

<per category, count of pending items>

- slop-removal: 3 pending
- type-consolidation: 0 pending
- ...

## Suggested next runs

- `/optimize:apply --category <cat>` for each remaining category
- Or re-run `/optimize:scan` to refresh findings if the codebase has moved significantly
```

### 5. Print a short summary to the user

```
## Verify complete

Verdict:    <OK / REGRESSION>
Done:       N
Pending:    N  across <K> categories
Blocked:    N
Skipped:    N

Report: <out_dir>/verify-report.md
```

## Never do

- Do NOT modify source files.
- Do NOT touch git state (no stash, no reset, no commit). Verify is pure observation.
- Do NOT open the browser or run any MCP tools — verify is lint/typecheck/test/build only.
