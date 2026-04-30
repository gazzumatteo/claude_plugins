---
description: Validate an existing E2E checklist against the current code — flag obsolete/missing steps, ask followups, then update the file in place
argument-hint: <path-to-checklist.md> [--dry-run] [--since <git-ref>] [--no-memory]
allowed-tools: Read, Bash, AskUserQuestion, Task, Glob, Grep, mcp__plugin_claude-mem_mcp-search__search, mcp__plugin_claude-mem_mcp-search__smart_search
---

# Validate E2E checklist

You orchestrate a review of an existing checklist. You do NOT judge the file yourself — that is the `checklist-auditor`'s job. You do NOT rewrite it — that is the `checklist-updater`'s job. You sit in the middle: gather context, delegate, ask the user, apply approved changes.

## Arguments

`$ARGUMENTS` contains: the checklist path, plus optional flags:

- `--dry-run` → produce the audit report but do NOT modify the file
- `--since <git-ref>` → consider only changes after this ref (default: the commit that last touched the checklist file)
- `--no-memory` → skip the claude-mem lookup (e.g. when the MCP is unavailable)

## Steps you execute in order

### 1. Resolve and parse the existing checklist

Verify the path exists with `Bash(realpath <path>)`. Run:

```
${CLAUDE_PLUGIN_ROOT}/scripts/parse_checklist.py <abs-path> --out /tmp/e2e-validate-current-<ts>.json
```

Stdout is a small summary; **do not `Read` the full JSON**. From the summary, note `shape`, `step_count`, `title`, `source_sha256`, `out_path`. The shape is load-bearing — the updater must preserve it.

If `shape=unknown`, stop and report — the file is not a valid checklist as-is.

### 2. Determine the comparison baseline

Unless `--since` is provided, find the commit that last modified the checklist:

```
git log -1 --format=%H -- <checklist-path>
```

Save as `<baseline>`. If the file is untracked, use `git rev-parse HEAD` and note that all current changes are candidates.

### 3. Identify what changed since the baseline

Run:

```
git log --pretty=format:'%h %s' <baseline>..HEAD
git diff --stat <baseline>..HEAD
git diff --name-only <baseline>..HEAD
```

Save outputs. Scan the changed filenames for categories: routes, components, API handlers, auth, migrations, config. This is the raw input the auditor will use.

### 4. Query claude-mem for project context (unless `--no-memory`)

Check whether the claude-mem MCP tools are available (tools beginning with `mcp__plugin_claude-mem_mcp-search__`). If they are:

- Call `mcp__plugin_claude-mem_mcp-search__search` with query terms derived from the checklist title and section names (up to 3 focused queries, not a flood).
- If results look promising, call `mcp__plugin_claude-mem_mcp-search__smart_search` for a richer follow-up on the most relevant thread.
- Collect: past decisions about this feature, known bugs, deferred work, stakeholder asks.

If the MCP is not available OR returns nothing useful, note "no prior project memory found" and move on. Do NOT fabricate.

### 5. Read the config; record credentials path only

If there's a `.e2e-testing.yml`, `Read` it (small file). If the summary has `credentials_ref`, record the path but do NOT read its content here — pass the path to the auditor and let the auditor decide whether to read it. This keeps secrets out of the orchestrator session.

### 6. Delegate the audit to `checklist-auditor`

Use the `Task` tool with `subagent_type: checklist-auditor`. Pass:

- Absolute path to the parsed checklist JSON (from step 1's `out_path`)
- Absolute path to the original markdown file (read-only to the auditor)
- `baseline` commit SHA and the git log / diff outputs from step 3
- The memory notes from step 4 (a short prose paragraph, or "no memory available")
- Path to credentials file and `.e2e-testing.yml` (if any) — auditor reads on demand
- The repo root path
- Return-message contract: "Your final reply must be one line: `AUDIT_COMPLETE <audit-json-path>`. Save the full report to `/tmp/e2e-validate-audit-<ts>.json` and reply with the path."

Read the audit JSON via `jq` for specific fields you need; do not load the whole document.

### 7. Present findings and ask followup questions

Use `jq` to extract only what you need from the audit JSON (counts, ambiguous-titles, high-confidence obsolete/missing). Avoid `Read` on the audit file. For every item the auditor marked as needing user input (typically everything in `ambiguous[]` and any `missing[]` where the fix is non-obvious), batch into a single `AskUserQuestion` call.

Group the questions sensibly — up to 4 per batch. If there are more than 4, ask the top 4 first, apply, then ask the rest. Each question offers:

- "Keep the step as-is" — no change
- "Remove the step" — if obsolete
- "Update with auditor's suggestion" — showing the diff
- "Let me describe the change" — Other option

Also ask whether to proceed with the auditor's unambiguous recommendations (obsolete + missing with `confidence=high`) as a group: one "yes/no/review each" question.

### 8. Build the change-set

Based on answers + the auditor's unambiguous recommendations, assemble a change-set JSON:

```json
{
  "shape": "<current shape — MUST preserve>",
  "operations": [
    { "op": "remove", "step_id": "3.2", "reason": "endpoint deleted in commit a1b2c3" },
    { "op": "add",    "section": "New feature X", "step_id": "4.5", "action": "…", "expected": "…" },
    { "op": "update", "step_id": "2.1", "field": "expected", "new_value": "…" },
    { "op": "rename_section", "from": "Login", "to": "Autenticazione" }
  ]
}
```

Save to `/tmp/e2e-validate-changeset-<timestamp>.json`.

### 9. Handle `--dry-run`

If `--dry-run`: print a summary of the change-set (counts per op) and the path to the audit JSON. STOP. Do NOT modify the file.

### 10. Delegate the rewrite to `checklist-updater`

Use the `Task` tool with `subagent_type: checklist-updater`. Pass:

- Absolute path to the original markdown file
- Absolute path to the change-set JSON
- The shape (load-bearing — the updater must preserve it)

The updater applies the ops, writes the file back, and re-runs the parser to confirm nothing broke.

### 11. Verify post-update

Re-parse with `--out /tmp/e2e-validate-after-<ts>.json` and use only the small stdout summary. Confirm:

- `shape` unchanged
- `step_count` matches the expected post-change count (original + added − removed)
- `source_sha256` differs from the pre-update value (if any real change was made)

If any check fails, stop and show the user: the file may have been partially updated — `git diff` is their friend.

### 12. Present summary

Print:

```
## Checklist validated

File:   <path>
Shape:  <shape>  (preserved)
Steps:  <before> → <after>
  - Added:    N
  - Removed:  N
  - Updated:  N
  - Unchanged: N

Audit report: /tmp/e2e-validate-audit-<timestamp>.json

Next:
  git diff <path>       # review changes
  /run-checklist <path> # execute updated checklist
```

## Error handling

- Parse of original file fails → stop; the file is broken and should be fixed manually or recreated with `/create-checklist`
- Auditor returns `error` field → surface it, stop
- User rejects every suggestion → no changes; skip updater; print "no changes applied"
- Updater fails the post-parse → stop; the file was left in its pre-update state if the updater rolled back, otherwise show `git diff` for inspection
- claude-mem MCP unavailable AND `--no-memory` not passed → warn once, proceed

## Never do

- Never write the file directly — always go through the updater agent
- Never change the shape of the checklist (table stays table, etc.) unless the user explicitly asked
- Never apply changes without a prior `AskUserQuestion` round for ambiguous items
- Never skip the memory lookup silently when it was requested — if it errors, tell the user
- Never re-run `/run-checklist` automatically after updating
