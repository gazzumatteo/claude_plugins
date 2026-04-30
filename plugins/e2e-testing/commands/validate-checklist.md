---
description: Validate an existing E2E checklist against the current code — flag obsolete/missing steps, ask followups, then update the file in place
argument-hint: <path-to-checklist.md> [--dry-run] [--since <git-ref>] [--no-memory]
allowed-tools: Task
---

# Validate E2E checklist (dispatcher)

This command is a thin dispatcher. All orchestration runs in the `e2e-validator` subagent — an isolated context with a minimal tool set, so the work fits in Sonnet 4.6's standard 200K window regardless of the MCP servers configured in the caller's session.

## What you do (and only this)

1. Invoke the `e2e-validator` subagent via `Task`. Pass:
   - `subagent_type`: `e2e-validator`
   - `description`: `Validate E2E checklist`
   - `prompt`: a single string in this exact shape:
     ```
     ARGS: $ARGUMENTS
     CWD:  <current working directory, absolute>
     ```

2. When the subagent returns, print its final reply to the user **verbatim**. Do not summarize, paraphrase, or annotate. Do not run any other tools — `Task` is the only tool you have.

Contract tokens such as `VALIDATE_FAILED` or `VALIDATE_DRY` in the subagent's reply are intentional — forward them as-is.
