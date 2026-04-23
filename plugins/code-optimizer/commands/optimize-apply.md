---
description: Apply optimization findings in batches with regression guarding. Records a baseline, applies each batch via the executor, re-runs baseline, rolls back on regression. Never commits without explicit authorization.
argument-hint: [--category <name>] [--batch-size N] [--dry-run] [--force-risky] [--keep-snapshots] [--out <dir>]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Task, AskUserQuestion
model: sonnet
---

# Optimize: apply

You apply a prioritized checklist of optimization findings against the codebase, one batch at a time, with a regression-safety net. **Source files** change only through the `optimization-executor` subagent — the scope-violation check in step C enforces this. Your own `Write/Edit` tools are for plugin artifacts under `<out_dir>/` (checklist, apply-session JSON) and never for source files under the repo tree. You orchestrate: read checklist → baseline → batch → guard → commit or rollback → next batch.

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

Create a persistent snapshot that covers tracked state, AND take a separate snapshot of the current untracked-files list so rollback can distinguish files the executor added from files the user already had untracked:

```
batch_id="$(date +%s)-$(openssl rand -hex 3 2>/dev/null || echo $RANDOM)"
snap=$(git stash create)                          # commit-ish of tracked+staged state; "" if nothing to stash
head=$(git rev-parse --verify HEAD 2>/dev/null || true)
# Persist the snapshot ref so it cannot be GC'd and survives if Claude restarts:
if [[ -n "$snap" ]]; then
  git update-ref "refs/code-optimizer/batch-$batch_id" "$snap"
elif [[ -n "$head" ]]; then
  git update-ref "refs/code-optimizer/batch-$batch_id" "$head"
fi
# Snapshot untracked files so rollback doesn't rm files the user already had:
mkdir -p "<out_dir>/batches/$batch_id"
git ls-files --others --exclude-standard > "<out_dir>/batches/$batch_id/untracked-before.txt"
```

Record in `<out_dir>/apply-session.json` under this batch (use `Write` or `Edit` to merge into the session file):

```json
{ "batch_id": "<id>", "snap_ref": "refs/code-optimizer/batch-<id>", "head": "<sha>", "created_files": [], "untracked_before_path": "<out_dir>/batches/<id>/untracked-before.txt" }
```

**Writes to `apply-session.json` must be eager, not deferred.** Every step that changes session state is a mandatory checkpoint — persist IMMEDIATELY, do not accumulate changes in memory and flush at the end of the session:

- After step B (snapshot taken): append the new batch entry.
- After step C, per finding: append the finding id to `applied_ids`, `blocked_ids`, or `errored_ids` depending on the executor's verdict; append any `files_created` to the batch entry.
- After step E (guard verdict): write the verdict and any regression notes onto the batch entry.
- After step F (commit/stage): record the commit SHA (if any) and staged path count.

A crashed or interrupted session must leave `apply-session.json` consistent with the on-disk state of the repo. If the user's next `/optimize:apply` sees `applied_ids: []` but finds a populated `batches/` directory, the previous run violated this rule.

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

After the executor returns, enforce the scope guard against the **finding's ground truth**, not against whatever the executor self-reports in `files_touched`. An executor that silently extends scope (e.g. editing a test suite to fix a mock broken by the refactor) must be caught here, even if its response dutifully lists the extra path in `files_touched`.

1. Build `allowed_modify_paths`: take `finding.files`, strip any `:<start>-<end>` line-range suffix from each entry, and collect the remaining file paths.
2. Build `allowed_create_paths`: the `files_created[]` returned by the executor, filtered through a whitelist — each new path MUST be under a neutral domain location (`src/`, `app/`, `lib/`, `packages/*/src/`) AND must NOT match any of: `**/*.test.*`, `**/*.spec.*`, `**/__tests__/**`, `**/__mocks__/**`, `**/fixtures/**`, `**/snapshots/**`, `**/*.snap`, `package.json`, `pyproject.toml`, `tsconfig*.json`, `.github/**`, `**/ci/**`. Any `files_created` entry failing this filter = scope violation.
3. Run `Bash git diff --name-only HEAD` → the set `modified_paths`. Every entry must be in `allowed_modify_paths`. A diff touching a path outside that set = scope violation — even if the executor listed it in its own `files_touched` response.
4. Run `Bash git ls-files --others --exclude-standard` → the set `new_untracked_paths`. Every entry outside `<out_dir>/` must be in `allowed_create_paths`.

If ANY of the three checks fails → **abort the batch immediately**: go straight to the rollback sequence in step E as if a regression occurred, and mark the item `[~]` with note `scope-violation: <offending path> (<which check>)`. Do NOT accept the executor's self-report as authoritative.

### D. In dry-run mode

Gather all previews and write them to `<out_dir>/batches/<batch_id>/preview.md`. Do NOT invoke regression-guard. Mark items as "previewed" in a scratch file (NOT in the checklist).

Then clean up the per-batch state created in step B — dry-run batches must not leak:

- `Bash git update-ref -d refs/code-optimizer/batch-<batch_id>` (drop the ref).
- Keep `<out_dir>/batches/<batch_id>/preview.md` so the user can review, but remove `<out_dir>/batches/<batch_id>/untracked-before.txt` (`Bash rm -f -- <path>`).

**Skip steps E and F entirely** and continue to the next batch.

### E. Otherwise, guard against regression

Use `Task` with `subagent_type: regression-guard`:

- `repo_root`, `out_dir`
- `baseline_path`: `<out_dir>/baseline.json`
- `commands`: same as used for baseline
- `batch_id`

Expect a verdict JSON:

- `verdict: "ok"` — mark every APPLIED item in the batch as `[x]` in the checklist. Record the new commit SHA (if commit strategy ≠ `stage_only`) in apply-session. **By default, drop the per-batch ref now** to avoid leaking refs across sessions: `Bash git update-ref -d refs/code-optimizer/batch-<id>`. If `--keep-snapshots` was passed, retain the ref for forensic rollback.
- `verdict: "regression"` — ROLLBACK. Rollback must cover both tracked changes AND untracked files the executor created (but MUST NOT touch files the user already had untracked):
  1. `Bash git reset --hard refs/code-optimizer/batch-<id>` — restores tracked files.
  2. Read `<out_dir>/batches/<batch_id>/untracked-before.txt`. For each path in the batch's `created_files[]` (from the session JSON): if the path is **NOT** listed in `untracked-before.txt` AND the path is not tracked (`git ls-files --error-unmatch <path>` returns non-zero) AND it exists on disk, remove it with `Bash rm -f -- <path>`. Skip any path that was already in `untracked-before.txt` — that file was the user's work, untouched by the executor, and must be preserved. NEVER use `git clean -fd`.
  3. Mark each item in the batch as `[~]` with note "regression: <phase> <diff summary>". Keep the ref `refs/code-optimizer/batch-<id>` so the user can re-inspect the failed batch.
  4. `AskUserQuestion`: "Continue with next batch or abort?" — continue / abort.
  5. **No implicit retry.** A regressed (or scope-violating) batch is **final** for this session. Do NOT re-invoke the executor on the same finding with a different approach, do NOT create a parallel batch directory (e.g. `<out_dir>/batches/<batch_id>-retry/`), do NOT mutate `<batch_id>` to try again. The finding stays `[~]` until the user re-runs `/optimize:apply` explicitly, or re-plans the scope, or passes `--force-risky`. Silent retries are how orphan batch directories and double-spent snapshot refs happen.

### F. Commit according to strategy

The plan's commit strategy (from `<out_dir>/plan.json`) is one of:

- `stage_only`: `Bash git add <touched files>` — no commit.
- `commit_per_batch`: `git add` + `git commit -m "optimize: <category> batch <batch_id>"`. Ask the user before the first commit of the session.
- `commit_per_category`: stage until category changes, then commit all.

NEVER use `--no-verify`. NEVER `git push`. If a pre-commit hook fails, report, do NOT retry with `--no-verify`, wait for user guidance.

## Post-session

After the loop ends (or user aborts):

1. Update `<out_dir>/checklist.md` with final statuses.
2. Write `<out_dir>/apply-session.json` with summary: applied / skipped / blocked / errored (most fields should already be populated by the eager writes — this step only finalizes `completed_at` and rolls up the summary counts).
3. **Cleanup `<out_dir>/batches/`**:
   - Delete any `.DS_Store` (or analogous OS-noise) files anywhere under `<out_dir>/batches/`.
   - For each subdirectory of `<out_dir>/batches/`: if the corresponding batch in `apply-session.json` has verdict `ok` AND `--keep-snapshots` was NOT passed, remove the subdirectory (the snapshot ref was already deleted in step E). If verdict is `regression` or `scope-violation`, KEEP the subdirectory for forensic inspection. If the subdirectory has no corresponding batch entry in `apply-session.json` (orphan from an interrupted earlier run or a spontaneous retry), delete it.
   - Never `rm -rf` outside `<out_dir>/batches/`. Cleanup is scoped to this directory only.
4. Print summary:

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
