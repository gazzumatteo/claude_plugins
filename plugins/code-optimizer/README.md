# code-optimizer

Scan, plan, apply, and verify code optimizations across a project — with a regression-safety net that rolls back any batch that worsens lint / typecheck / tests / build.

Built for the reality of AI-assisted codebases: duplications that "look" similar but shouldn't be merged, types scattered with silent drift, `any` left as a placeholder, try/catch blocks that swallow failures, and comments that narrate edit history instead of explaining intent.

## The four commands

| Command | What it does |
|---|---|
| `/optimize:scan` | Read-only audit across 11 categories → `findings.json` + scan report |
| `/optimize:plan` | Turn findings into a prioritized, user-approved checklist |
| `/optimize:apply` | Apply the checklist in batches with regression guard + rollback |
| `/optimize:verify` | Final verdict against the recorded baseline |

## The 11 categories

| Category | What it finds |
|---|---|
| **deduplication** | Repeated logic and copy-pasted functions (honest dedup, not lookalikes) |
| **type-consolidation** | Scattered type definitions with silent drift |
| **dead-code** | Unused files, exports, deps — manually verified before removal |
| **circular-deps** | Import cycles mapped + proposals to extract shared logic |
| **type-strengthening** | `any` / `unknown` placeholders → concrete types |
| **error-handling** | Try/catch blocks that swallow, mask, or hide failures |
| **slop-removal** | AI artifacts, edit-history comments, deprecated paths |
| **complexity** | Long functions, deep nesting, high cognitive complexity |
| **magic-constants** | Hardcoded numbers/strings → named constants |
| **naming** | Same concept with different names across the codebase |
| **excessive-parameters** | Functions with > 4 params or ≥ 2 boolean flags |

Each category has an entry in `skills/optimization-playbook/SKILL.md` with detection criteria, red flags (what NOT to touch), a safe-refactor recipe, and regression indicators.

## Supported ecosystems

| Ecosystem | Static tools leveraged (when available) |
|---|---|
| TypeScript / JavaScript | knip, madge, jscpd, eslint-plugin-sonarjs |
| Python | vulture, radon, ruff, pyright/mypy |
| Go | go vet, go build, go test (baseline only) |
| Other (Rust, Dart, PHP, Ruby, ...) | Claude semantic analysis only |

All tools are opt-in: the plugin detects what the project already has and uses it. Missing tools are not installed — scanners that depend on them simply emit fewer, more conservative findings.

## Regression safety net

Every `/optimize:apply` session:

1. Records a **baseline**: runs the project's own lint / typecheck / tests / build and captures exit codes + counts + log hashes.
2. Applies findings **one batch at a time** (default batch size: 1).
3. Before each batch, saves a persistent snapshot as a git ref under `refs/code-optimizer/batch-<id>` (won't be GC'd, survives Claude restarts). Tracks any new files the executor creates so rollback can remove them precisely.
4. After the executor returns, verifies the diff stays within the finding's declared files (aborts the batch on scope-violation).
5. Re-runs the baseline and **diffs**. Any phase that worsened (new errors, failed tests, non-zero exit) → the batch is **rolled back**: `git reset --hard refs/code-optimizer/batch-<id>` restores tracked files, plus a targeted `rm` of any untracked file the executor created (never a bare `git clean` — your other untracked work is safe).
6. Blocked items are marked `[~]` in the checklist; the user decides whether to continue or abort.
7. `/optimize:verify` produces the final verdict against the baseline and lists the preserved `refs/code-optimizer/*` so you can forensically roll back any batch.

The plugin never commits or pushes without explicit user authorization, never uses `--no-verify`, and never broadens a change beyond the files listed in a finding.

## Per-project configuration

Create `.code-optimizer.yml` at the repo root:

```yaml
languages: auto
categories: [deduplication, dead-code, slop-removal, type-consolidation]
commands:
  lint: "npm run lint"
  typecheck: "npm run typecheck"
  test: "npm test"
  build: "npm run build"
paths:
  include: ["src/**"]
  exclude: ["**/node_modules/**", "**/dist/**"]
batch_size: 1
commit_strategy: stage_only    # or commit_per_batch / commit_per_category
allow_review_required: false
```

See `.code-optimizer.example.yml` for the full schema.

## Output layout

```
.optimize/
├── ecosystem.json              # from detect_ecosystem.sh
├── raw/                        # static tool outputs (knip.json, madge.json, jscpd.json, ...)
├── findings-per-scanner/       # one <category>.json per scanner
├── findings.json               # consolidated, sorted
├── optimize-scan-report.md     # human-readable summary
├── plan.json                   # user preferences from /optimize:plan
├── checklist.md                # the execution checklist with checkboxes
├── baseline.json               # regression baseline (lint/typecheck/test/build)
├── baseline.lint.log
├── baseline.typecheck.log
├── baseline.test.log
├── baseline.build.log
├── batches/<batch_id>/         # per-batch: fresh baseline + preview or diff
├── apply-session.json          # session summary
└── verify-report.md            # from /optimize:verify
```

Add `.optimize/` to your `.gitignore`.

## Typical workflow

```bash
# 1. Full audit
/optimize:scan

# 2. Review findings, build a plan
/optimize:plan

# 3. Start with the lowest-risk categories first
/optimize:apply --category slop-removal
/optimize:apply --category magic-constants

# 4. Then higher-risk, with user confirmation per item
/optimize:apply --category dead-code --force-risky

# 5. Final verification
/optimize:verify
```

`--dry-run` on `/optimize:apply` shows the proposed diff for each finding without touching any file — useful for risky categories before committing.

## What this plugin will never do

- Commit or push without explicit user authorization.
- Use `--no-verify` or any other hook-bypass flag.
- Install, upgrade, or remove tools from the target project.
- Modify files outside a finding's declared scope.
- Merge two functions that look identical but serve different domains.
- Remove code that static analysis says is unused without a widened grep + framework-convention check.
- Narrate its own edits in comments it adds to the code.

## Agents shipped with this plugin

Orchestration:
- `optimization-architect` — dispatches the scanners in parallel, aggregates output
- `baseline-recorder` — captures the regression baseline
- `regression-guard` — re-runs + diffs after each batch
- `optimization-executor` — applies a single finding per call, following the playbook

Scanners (one per category):
- `dedup-scanner`, `type-consolidator`, `dead-code-hunter`, `cycle-mapper`,
  `type-strengthener`, `error-handler-auditor`, `slop-remover`,
  `complexity-auditor`, `constants-hunter`, `naming-auditor`, `parameter-auditor`

## Requirements

- Claude Code CLI
- `git` (for snapshot / rollback)
- Python 3.10+ (for the aggregator + baseline diff scripts)
- The project's own tool stack (optional): npm, pnpm, yarn, uv, poetry, pip, go, cargo

No extra dependencies are added to your project. Every tool the plugin calls (knip, madge, jscpd, vulture, radon) is run via `npx` / `uvx` / PATH — if not already present it's simply skipped, and the scanners fall back to semantic analysis.
