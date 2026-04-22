---
description: Execute an E2E test checklist (markdown) via browser automation and produce a structured report
argument-hint: <path-to-checklist.md> [--fast] [--dry-run] [--only ids] [--from N]
allowed-tools: Read, Bash, AskUserQuestion, Task, Glob, Grep
model: sonnet
---

# Run E2E checklist

You orchestrate the execution of a test checklist. You do NOT execute tests yourself — that is the `test-executor` subagent's job. You prepare the run, delegate, and audit.

## Arguments

`$ARGUMENTS` contains: the path to the checklist file and optional flags.

Parse flags:
- `--fast` → pass to test-executor as a signal to use Haiku model
- `--dry-run` → parse and validate, do NOT execute
- `--only <ids>` → comma-separated step IDs to execute (e.g. `3.1,3.2,14.5`)
- `--from <N>` → resume from step N (skip earlier steps)

## Steps you execute in order

### 1. Resolve the file

Parse `$ARGUMENTS` to extract the checklist path (first non-flag argument). If the path is relative, resolve it against the current working directory. Use `Bash(realpath <path>)` or `Read` to verify the file exists. If it doesn't, stop and print an error.

### 2. Parse the checklist

Run:
```
${CLAUDE_PLUGIN_ROOT}/scripts/parse_checklist.py <absolute-path>
```

Save stdout as the parsed JSON. Write it to `/tmp/e2e-parsed-<timestamp>.json` so it's available to subsequent commands. Read it and note: `title`, `shape`, `step_count`, `prereqs`, `credentials_ref`, and how many steps have `destructive: true`.

### 3. Check for per-project config

Look for `.e2e-testing.yml` in:
1. Directory of the checklist file
2. Parent directory (repo root)

Use `Glob` to find. If found, `Read` it and extract: `base_url`, `credentials_file`, `pre_run`, `post_run`, `auto_confirm_destructive`.

### 4. Read credentials reference (if any)

If the parsed JSON has `credentials_ref` OR the per-project config points to a credentials file, `Read` that file and note: what logins, accounts, tokens are documented.

### 5. Identify missing information (batched HITL)

Scan the parsed prereqs and per-project config. Identify gaps:
- Does the checklist reference URLs that aren't in config and aren't in the checklist itself? → gap
- Does it reference credentials that aren't in the credentials file? → gap
- Are there destructive steps and `auto_confirm_destructive` is not true? → gap (need explicit per-step confirmation policy)

If there are ANY gaps, use `AskUserQuestion` ONCE with all gaps batched as separate questions. Do not ask one-per-step later.

Example batched questions:
- "What URL should be used as the base for the portale Firebase step?" (options: extracted defaults + Other)
- "Credentials for admin user?" (options: from credentials file + Other)
- "Proceed with destructive actions without asking each time?" (yes / no / ask each)

### 6. Run pre-run hooks

If per-project config defines `pre_run`, execute each command via `Bash`. If any fails, stop and report.

### 7. Initialize the report

Run:
```
${CLAUDE_PLUGIN_ROOT}/scripts/init_report.py /tmp/e2e-parsed-<timestamp>.json --executor <model> --browser <browser>
```

Where `<model>` is `claude-haiku-4-5` if `--fast`, else `claude-sonnet-4-6`. Where `<browser>` is from config or defaults to `chromium`.

Save stdout — it contains paths to json_report, md_report, evidence_dir, current_source_marker.

### 8. Handle --dry-run

If `--dry-run` was passed: print a summary (title, shape, step_count, prereqs summary, destructive count, output paths) and STOP here. Do not execute.

### 9. Filter steps (if --only or --from)

If `--only` or `--from`, modify the run JSON in place (via Bash + jq or Python one-liner) to set steps outside the filter to `SKIPPED` with note `"filtered by --only/--from"`. This way the executor still sees the full result array but only touches filtered steps.

### 10. Delegate to test-executor

Use the `Task` tool to invoke the `test-executor` subagent. Pass to it, in the prompt:

- Absolute path to the run JSON
- Absolute path to the evidence directory
- The credentials dict (URLs, usernames, passwords gathered in step 5) — these are YOUR session context, never written to disk
- The destructive policy (confirm-each, auto-approve, or deny)
- The user's answers from step 5

The executor will return when every step has a final status.

### 11. Run post-run hooks

If per-project config defines `post_run`, execute each command via `Bash`.

### 12. Run the audit

```
${CLAUDE_PLUGIN_ROOT}/scripts/audit_report.py <run-json-path>
```

Read the JSON output. Note: verdict, errors, warnings, downgraded count, status counts.

### 13. Present summary to the user

Print a concise summary:

```
## E2E run complete

Source: <path>
Verdict: <PASSED | BUGS_FOUND | UNVERIFIED | FAILED>

Results:
- PASS: N
- FAIL: N  (see bugs below)
- BLOCKED: N
- UNVERIFIED: N  (downgraded from PASS — no evidence)
- SKIPPED: N

Report: <md path>
JSON:   <json path>

Top bugs:
1. [severity] <title>  →  step <ref>
2. [severity] <title>  →  step <ref>
```

Do NOT propose fixes. Do NOT offer to patch bugs. Your job ends with the report.

## Error handling

- Parser fails → stop, print stderr, suggest the user inspect the file
- `init_report.py` fails → stop
- Pre-run hook fails → stop, report which hook failed
- Executor subagent reports an error → still run the audit (partial results are useful)
- Audit verdict `FAILED` (source file changed, count mismatch) → surface the error prominently

## Never do

- Do not modify the source checklist file (the hook enforces this; you should not try anyway)
- Do not write code to fix any bug the executor reports
- Do not re-run the suite automatically after a failure — that's the user's call
- Do not skip the audit step even if the executor reports everything as PASS
