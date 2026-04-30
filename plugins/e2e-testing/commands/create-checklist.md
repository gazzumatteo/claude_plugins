---
description: Create a new E2E test checklist (markdown) by analyzing the project and emitting a file ready for /run-checklist
argument-hint: <feature-or-path-or-description> [--shape table|prose|nested|cli] [--out <path>] [--fast]
allowed-tools: Task
---

# Create E2E checklist (dispatcher)

This command is a thin dispatcher. All orchestration runs in the `e2e-creator` subagent — an isolated context with a minimal tool set, so the work fits in Sonnet 4.6's standard 200K window regardless of the MCP servers configured in the caller's session.

## What you do (and only this)

1. Invoke the `e2e-creator` subagent via `Task`. Pass:
   - `subagent_type`: `e2e-creator`
   - `description`: `Create E2E checklist`
   - `prompt`: a single string in this exact shape:
     ```
     ARGS: $ARGUMENTS
     CWD:  <current working directory, absolute>
     ```

2. When the subagent returns, print its final reply to the user **verbatim**. Do not summarize, paraphrase, or annotate. Do not run any other tools — `Task` is the only tool you have.

Contract tokens such as `CREATE_FAILED` or `CREATE_ABORTED` in the subagent's reply are intentional — forward them as-is.
