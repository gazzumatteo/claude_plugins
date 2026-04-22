---
description: Apply optimization findings in batches with regression guarding. Records a baseline, applies each batch via the executor, re-runs baseline, rolls back on regression. Never commits without explicit authorization.
argument-hint: [--category <name>] [--batch-size N] [--dry-run] [--force-risky] [--out <dir>]
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
model: sonnet
---

# Optimize: apply

You apply a prioritized checklist of optimization findings against the codebase, one batch at a time, with a regression-safety net. Source files change only through the `optimization-executor` subagent. You orchestrate: read checklist → baseline → batch → guard → commit or rollback → next batch.

## Arguments

- `--category <name>` — only items in this category. Default: every item.
- `--batch-size N` — number of findings per batch. Default: 1 (most conservative; each finding gets its own regression check).
- `--dry-run` — executor produces a diff preview; no files are modified and no batches are staged.
- `--force-risky` — allow items with `requires_manual_review: true`. User confirms each one via `AskUserQuestion` regardless.
- `--out <dir>` — default: `.optimize`.

## Pre-flight

### 1. Resolve state

- `Glob` for `<out_dir>/plan.json` and `<out_dir>/checklist.md`. If either is missing → stop, instruct user to run `/optimize:plan`.
- `Glob` for `<out_dir>/findings.json`. `Read` both JSON files + the checklist markdown.
- Build the list of pending items (checkbox `[ ]`), matching `--category` if provided.
- If no pending items → report "nothing to do" and stop.

### 2. Verify working tree is clean

Run: `Bash git status --porcelain`. If non-empty:

- `AskUserQuestion`:
  - "Working tree has uncommitted changes. Stash them and continue?" — yes / no (abort).
- If yes: `Bash git stash push -u -m "code-optimizer pre-apply $(date -u +%Y%m%d-%H%M%S)"`. Record the stash ref in `<out_dir>/apply-session.json`.

Destructive operations like `git reset --hard` should NEVER be used pre-emptively. Only on explicit rollback after a regression (see below).

### 3. Record baseline

Use the `Task` tool with `subagent_type: baseline-recorder`:

- `repo_root`, `out_dir`
- `commands` from `.code-optimizer.yml` or ecosystem auto-detect

Expect the recorder to write `<out_dir>/baseline.json`. If the recorder returns an error JSON, surface and stop.

If baseline shows the project is already failing (lint/typecheck/test), warn the user but continue — the regression guard compares against the baseline, not against "green".

## Per-batch loop

Repeat while pending items remain (respecting `--category` and `--batch-size`):

### A. Select next batch

- Take the next N pending items from the checklist (N = `--batch-size`).
- For each item with `requires_manual_review: true`:
  - If `--force-risky` was passed: `AskUserQuestion` "Proceed with <id> (<risk>)? — <reason>" with options yes / skip / abort session.
  - If `--force-risky` NOT passed: mark the item `[-]` with note "skipped: needs manual review" and remove from the batch.

If the batch is empty after filtering, move to the next batch.

### B. Snapshot HEAD + working tree for rollback

Create a persistent snapshot that covers BOTH tracked and untracked state, so rollback is complete even if the executor added new files:

```
batch_id=$(date +%s)-<shortid>
snap=$(git stash create)                          # commit-ish of tracked+staged state; "" if nothing to stash
head=$(git rev-parse --verify HEAD 2>/dev/null || true)
# Persist the snapshot ref so it cannot be GC'd and survives if Claude restarts:
if [[ -n "$snap" ]]; then
  git update-ref "refs/code-optimizer/batch-$batch_id" "$snap"
elif [[ -n "$head" ]]; then
  git update-ref "refs/code-optimizer/batch-$batch_id" "$head"
fi
```

Record in `<out_dir>/apply-session.json` under this batch:

```json
{ "batch_id": "<id>", "snap_ref": "refs/code-optimizer/batch-<id>", "head": "<sha>", "created_files": [] }
```

### C. Execute each finding in the batch

For each finding `f`, use `Task` with `subagent_type: optimization-executor`:

- `finding`: the full Finding object from `findings.json`
- `repo_root`, `playbook_path`
- `dry_run`: the `--dry-run` flag
- `force_risky`: the `--force-risky` flag

Collect the returned status JSON. Possible outcomes:

- `APPLIED` — changes on disk. Capture `files_touched[]` and `files_created[]` from the response; append `files_created` to the batch's `created_files` list in `apply-session.json`.
- `DRY_RUN_OK` — preview only (no changes)
- `SKIPPED_NEEDS_REVIEW` — no changes, mark `[-]`
- `ERROR` — no changes, mark `[~]` with the error

After the executor returns, run `Bash git diff --name-only HEAD` and `Bash git ls-files --others --exclude-standard`. Confirm every changed/new path is in `files_touched ∪ files_created`. If the executor modified a file outside its declared scope → **abort the batch immediately**: go straight to the rollback sequence in step E as if a regression occurred, and mark the item `[~]` with note "scope-violation: modified <path>".

### D. In dry-run mode

Gather all previews and write them to `<out_dir>/batches/<batch_id>/preview.md`. Do NOT invoke regression-guard. Mark items as "previewed" in a scratch file (NOT in the checklist). Move to the next batch.

### E. Otherwise, guard against regression

Use `Task` with `subagent_type: regression-guard`:

- `repo_root`, `out_dir`
- `baseline_path`: `<out_dir>/baseline.json`
- `commands`: same as used for baseline
- `batch_id`

Expect a verdict JSON:

- `verdict: "ok"` — mark every APPLIED item in the batch as `[x]` in the checklist. Record the new commit SHA (if commit strategy ≠ `stage_only`) in apply-session. You may now **drop the per-batch ref** once the batch is confirmed stable (optional — keeping them aids forensic rollback): `git update-ref -d refs/code-optimizer/batch-<id>`.
- `verdict: "regression"` — ROLLBACK. Rollback must cover both tracked changes AND untracked files the executor added:
  1. `Bash git reset --hard refs/code-optimizer/batch-<id>` — restores tracked files.
  2. For each path in the batch's `created_files[]` (from the session JSON): if it still exists on disk and is not tracked (`git ls-files --error-unmatch <path>` returns non-zero), remove it with `Bash rm -f -- <path>` (exact paths only; NEVER `git clean -fd` — that would blow away the user's unrelated untracked work including `.optimize/`).
  3. Mark each item in the batch as `[~]` with note "regression: <phase> <diff summary>".
  4. `AskUserQuestion`: "Continue with next batch or abort?" — continue / abort.

### F. Commit according to strategy

The plan's commit strategy (from `<out_dir>/plan.json`) is one of:

- `stage_only`: `Bash git add <touched files>` — no commit.
- `commit_per_batch`: `git add` + `git commit -m "optimize: <category> batch <batch_id>"`. Ask the user before the first commit of the session.
- `commit_per_category`: stage until category changes, then commit all.

NEVER use `--no-verify`. NEVER `git push`. If a pre-commit hook fails, report, do NOT retry with `--no-verify`, wait for user guidance.

## Post-session

After the loop ends (or user aborts):

1. Update `<out_dir>/checklist.md` with final statuses.
2. Write `<out_dir>/apply-session.json` with summary: applied / skipped / blocked / errored.
3. Print summary:

```
## Apply complete

Session:   <apply-session.json>
Applied:   <N>
Skipped:   <N>  (review-required)
Blocked:   <N>  (regression)
Errored:   <N>

Baseline:  <out_dir>/baseline.json
Batches:   <out_dir>/batches/

Next:
  /optimize:verify                # final sanity check
  /optimize:apply --category X    # run remaining categories
```

## Never do

- Never `git commit --no-verify`, `git push`, or `git push --force`.
- Never modify a file outside `finding.files` for the current item (enforced by executor, verified by you reading `git diff --name-only` before staging).
- Never skip the regression guard on a non-dry-run batch.
- Never `git stash drop` the pre-apply stash without asking the user.
- Never proceed on a regression without user confirmation.

## Error recovery

- If the executor errors mid-batch: stop the batch, rollback to the pre-batch snapshot, mark `[~]` with the error.
- If the regression guard itself crashes: treat as regression (rollback).
- If git operations fail mid-rollback: STOP — do NOT run more destructive commands. Surface the error and let the user take over.
