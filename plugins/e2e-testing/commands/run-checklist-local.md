---
description: Execute an E2E test checklist via the LOCAL executor (Playwright + LM Studio). Zero Claude tokens for browser automation. Falls back to /run-checklist if the local endpoint is missing.
argument-hint: <path-to-checklist.md> [--dry-run] [--headed] [--allow-destructive] [--max-iterations N] [--only IDS] [--from ID] [--to ID]
allowed-tools: Task
---

# Run E2E checklist — local executor (dispatcher)

Companion to `/run-checklist`. Runs the same checklist format, but offloads the browser-automation loop to a local model (LM Studio) instead of the Claude orchestrator. The trade-off: zero token spend on screenshots and tool calls, but you must have a vision-capable, tool-calling local model running and reachable.

When in doubt — or if the local endpoint isn't configured — use `/run-checklist` instead. The local path is purposely best-effort: it will refuse to run with an unreachable endpoint rather than degrading silently.

## What you do (and only this)

1. Invoke the `e2e-runner-local` subagent via `Task`. Pass:
   - `subagent_type`: `e2e-runner-local`
   - `description`: `Run E2E checklist (local)`
   - `prompt`: a single string in this exact shape:
     ```
     ARGS: $ARGUMENTS
     CWD:  <current working directory, absolute>
     ```

2. When the subagent returns, print its final reply to the user **verbatim**. Do not summarize, paraphrase, or annotate. Do not run any other tools — `Task` is the only tool you have.

If the subagent's reply starts with `RUN_FAILED`, `RUN_DRY`, or any other contract token, still forward it verbatim — the contract tokens are intentional and the user (or follow-up tooling) reads them.

## First-time setup (the agent will guide you)

The local executor requires:
- An OpenAI-compatible endpoint with a vision-capable, tool-calling model (e.g. LM Studio serving `nvidia/nemotron-3-nano-omni`)
- Endpoint config in `${CLAUDE_PLUGIN_ROOT}/scripts/.env.local` (copy from `.env.example`)
- Playwright Chromium installed locally (`playwright install chromium`)

The agent verifies all of the above and prompts you when anything is missing. To validate your setup independently before running a real checklist, run:

```bash
uv run plugins/e2e-testing/scripts/spike/spike_capability_check.py
```

It prints a `GO / NO-GO` verdict for the three capabilities the runner depends on (reachability, vision grounding, tool calling).
