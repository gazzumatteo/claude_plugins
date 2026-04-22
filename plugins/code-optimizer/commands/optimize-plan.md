---
description: Turn scan findings into a prioritized, checkable execution plan. Asks the user which categories to tackle and at what confidence threshold, then writes checklist.md.
argument-hint: [--out <dir>] [--min-confidence high|medium|low]
allowed-tools: Read, Write, Edit, Bash, Glob, AskUserQuestion
model: sonnet
---

# Optimize: plan

You turn a scan's findings into an actionable checklist. You do NOT modify source code; you only produce `<out_dir>/checklist.md` and update metadata.

## Arguments

- `--out <dir>` — default: `.optimize` (must match the scan's output dir).
- `--min-confidence <level>` — gate findings below this confidence out of the checklist. Default: `medium`.

## Steps

### 1. Locate the scan

- `Glob` for `<out_dir>/findings.json` at the repo root. If missing, stop and tell the user to run `/optimize:scan` first.
- `Read` the findings JSON. If empty, report "nothing to do" and stop.

### 2. Compute per-category counts

From the findings, build a summary:

| Category | Findings | High-risk | Needs review |
|---|---|---|---|

### 3. Ask the user (one batched round)

Use `AskUserQuestion`:

1. **"Which categories do you want to tackle in this run?"** (multiSelect) — list only categories with ≥ 1 finding. Default-select: `slop-removal, magic-constants, type-consolidation` (lowest-risk categories).
2. **"Minimum confidence to include?"** — options: `high only (safest)`, `high + medium`, `include low (aggressive)`. Default: `high + medium`.
3. **"Commit strategy?"** — options: `stage only, no commits (Recommended)`, `commit per batch`, `commit per category`. Default: stage only.
4. **"Allow risky items that require manual review?"** — options: `no, skip them (Recommended)`, `yes, include and confirm per item`. Default: no.

### 4. Filter and prioritize

From the filtered findings, sort by:

1. `risk` ascending (low-risk first)
2. `confidence` descending
3. `severity` descending
4. original `id` for stable order

Group by category in the final output.

### 5. Write the checklist

Output `<out_dir>/checklist.md`. Use this template (matches `templates/execution-checklist.md`):

```markdown
# Code optimization checklist

Generated from: `<out_dir>/findings.json`
Commit strategy: <strategy>
Min confidence: <level>
Allow review-required: <yes|no>

## Legend

- `[ ]` pending
- `[x]` done
- `[~]` blocked (regression detected)
- `[-]` skipped by user / manual review pending

---

## slop-removal (<N>)

- [ ] **SLOP-001** — <description>
  - files: `<file1>, <file2>`
  - action: <proposed_action>
  - risk: low · confidence: high

...
```

### 6. Write a plan-metadata JSON

Also write `<out_dir>/plan.json` with:

```json
{
  "generated_at": "<ISO8601>",
  "source": "<out_dir>/findings.json",
  "categories": ["..."],
  "min_confidence": "medium",
  "commit_strategy": "stage_only",
  "allow_review_required": false,
  "item_count": N
}
```

The executor and verify commands read this JSON to stay in sync with the user's choices.

### 7. Present summary

```
## Plan written

Checklist: <out_dir>/checklist.md
Items:     <N> across <K> categories
Ordering:  risk ascending, confidence descending, severity descending

Next:
  /optimize:apply                               # run every item
  /optimize:apply --category slop-removal       # run one category
  /optimize:apply --category slop-removal --dry-run
```

## Never do

- Do NOT modify source code.
- Do NOT run any scanner or static tool.
- Do NOT re-sort an existing checklist if it already has some `[x]` items — warn the user first (`AskUserQuestion` whether to preserve or regenerate).
