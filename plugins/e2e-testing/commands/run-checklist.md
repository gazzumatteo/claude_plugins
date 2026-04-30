---
description: Execute an E2E test checklist (markdown) via browser automation and produce a structured report
argument-hint: <path-to-checklist.md> [--fast] [--dry-run] [--only ids] [--from N]
allowed-tools: Task
---

# Run E2E checklist (dispatcher)

This command is a thin dispatcher. All orchestration runs in the `e2e-runner` subagent — an isolated context with a minimal tool set, so the work fits in Sonnet 4.6's standard 200K window regardless of the MCP servers configured in the caller's session.

## What you do (and only this)

1. Invoke the `e2e-runner` subagent via `Task`. Pass:
   - `subagent_type`: `e2e-runner`
   - `description`: `Run E2E checklist`
   - `prompt`: a single string in this exact shape:
     ```
     ARGS: $ARGUMENTS
     CWD:  <current working directory, absolute>
     ```

2. When the subagent returns, print its final reply to the user **verbatim**. Do not summarize, paraphrase, or annotate. Do not run any other tools — `Task` is the only tool you have.

If the subagent's reply starts with `RUN_FAILED`, `RUN_DRY`, or any other contract token, still forward it verbatim — the contract tokens are intentional and the user (or follow-up tooling) reads them.
