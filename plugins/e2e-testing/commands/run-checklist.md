---
description: Execute an E2E test checklist (markdown) via browser automation and produce a structured report
argument-hint: <path-to-checklist.md> [--fast] [--dry-run] [--only ids] [--from N]
allowed-tools: Read, Bash, AskUserQuestion, Task, Glob, Grep
model: sonnet
---

# Run E2E checklist

You orchestrate execution. The `test-executor` subagent does the actual testing. Your job: prepare, delegate, audit. Keep your own context lean — never `Read` the parsed checklist JSON or the run JSON in full; rely on script summaries and targeted `jq`/`grep` queries.

## Arguments

`$ARGUMENTS` = checklist path (first non-flag) plus flags:
- `--fast` → executor uses Haiku
- `--dry-run` → parse + summarize, no execution
- `--only <ids>` → comma-separated step IDs (e.g. `3.1,3.2`)
- `--from <N>` → resume from step N

## Steps

### 1. Resolve

`Bash(realpath <path>)` to get the absolute path. Stop if missing.

### 2. Parse — full JSON to disk, summary to stdout

```
${CLAUDE_PLUGIN_ROOT}/scripts/parse_checklist.py <abs-path> --out /tmp/e2e-parsed-<ts>.json
```

Stdout is a small summary: `title`, `shape`, `step_count`, `destructive_count`, `needs_browser_count`, `needs_cli_count`, `prereqs_count`, `credentials_ref`, `sections`, `out_path`. **Do not `Read` the full JSON.** If `shape=unknown` or `step_count=0`, stop and report.

### 3. Per-project config

`Glob` for `.e2e-testing.yml` next to the checklist or at repo root. If found, `Read` (small file). Note: `base_url`, `credentials_file`, `pre_run`, `post_run`, `auto_confirm_destructive`, `browser`.

### 4. Credentials path (don't read content)

If the parsed summary has `credentials_ref` or config has `credentials_file`, record the **path only**. Do not `Read` the file here — the executor will read it on demand. Reading creds into the orchestrator burns context and risks logging secrets.

### 5. Batched HITL for gaps

Identify gaps from the summary + config:
- destructive steps + no `auto_confirm_destructive` → confirmation policy needed
- no `base_url` and no URL in prereqs → ask
- credentials referenced but no file path resolved → ask

If any gaps, call `AskUserQuestion` ONCE with all gaps batched. Skip if none.

### 6. Pre-run hooks

If config has `pre_run`, execute each via `Bash`. Stop on first failure.

### 7. Init the run

```
${CLAUDE_PLUGIN_ROOT}/scripts/init_report.py /tmp/e2e-parsed-<ts>.json --executor <model> --browser <browser>
```

`<model>` = `claude-haiku-4-5` if `--fast` else `claude-sonnet-4-6`. `<browser>` = config or `chromium`. Stdout is a small JSON with `json_report`, `md_report`, `evidence_dir`, `current_source_marker`. Save those paths.

### 8. Dry-run exit

If `--dry-run`: print summary + paths and STOP.

### 9. Filter (optional)

If `--only` or `--from`, run a one-line jq/python script that **mutates the run JSON in place** and prints only `{filtered: N, kept: M}`. Do not echo the JSON.

Example:
```
python3 -c "import json,sys; p='<json>'; d=json.load(open(p)); keep=set('<ids>'.split(',')); n=0
for r in d['results']:
  if r['id'] not in keep: r['status']='SKIPPED'; r['notes']='filtered by --only'; n+=1
json.dump(d,open(p,'w'),indent=2,ensure_ascii=False); print({'filtered':n,'kept':len(d['results'])-n})"
```

### 10. Delegate to test-executor

Invoke `Task` with `subagent_type: test-executor`. Prompt MUST include:

- Absolute path to the run JSON
- Absolute path to the evidence directory
- Path to the credentials file (NOT its contents)
- Destructive policy from step 5 (`confirm-each` | `auto-approve` | `deny`)
- Any user answers from step 5 (URLs, picks, etc.)
- **Return-message contract**: "When done, your final reply to me must be one line: `RUN_COMPLETE <run-json-path>`. Do not summarize results. Do not include step-by-step output. The audit will read the JSON."

The executor returns when every step has a final status.

### 11. Post-run hooks

If config has `post_run`, run each via `Bash`.

### 12. Audit

```
${CLAUDE_PLUGIN_ROOT}/scripts/audit_report.py <run-json-path>
```

Stdout is the small audit dict: `verdict`, `errors`, `warnings`, `downgraded_count`, `status_counts`. Use it directly.

### 13. Top bugs (lean)

Get up to 5 bug headlines without reading the whole JSON:
```
jq -r '.bugs[0:5][] | "[\(.severity)] \(.step_ref) — \(.title)"' <run-json>
```

### 14. Present

```
## E2E run complete

Source:  <abs-path>
Verdict: <PASSED | BUGS_FOUND | UNVERIFIED | FAILED>

Counts:  PASS=N FAIL=N BLOCKED=N UNVERIFIED=N SKIPPED=N
Report:  <md path>
JSON:    <json path>

Top bugs:
1. [severity] <ref> — <title>
…
```

Stop here. Do not propose fixes, patches, or re-runs.

## Error handling

- Parser fails → stop; print stderr.
- `init_report.py` fails → stop.
- Pre-run hook fails → stop and name the hook.
- Executor errors → still run the audit (partial results matter).
- Audit verdict `FAILED` (source changed mid-run, count mismatch) → surface prominently.

## Never

- Do not `Read` the full parsed JSON, run JSON, or credentials file from this orchestrator session — pass paths to the executor instead.
- Do not modify the source checklist file (a hook blocks it).
- Do not write fix code or propose patches.
- Do not re-run automatically after a failure.
