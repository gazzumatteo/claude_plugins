---
description: Execute an E2E test checklist. Auto-routes to the cloud (Claude+Playwright) or local (LM Studio) executor based on $E2E_EXECUTOR.
argument-hint: <path-to-checklist.md> [--fast] [--dry-run] [--only ids] [--from N]
allowed-tools: Bash, Task
---

# Run E2E checklist (dispatcher)

This is the single entry point for running an E2E checklist. It picks the executor based on `E2E_EXECUTOR`, then delegates to the matching subagent. To force a specific executor regardless of env, use `/run-checklist-local` (force local).

## What you do (and only this)

### 1. Resolve the executor

Use `Bash` ONCE to read `E2E_EXECUTOR` from the cascade (lowest precedence first):

```bash
EXECUTOR=""
if [ -f "${PWD}/.e2e-testing.env" ]; then
  EXECUTOR=$(grep -E '^E2E_EXECUTOR=' "${PWD}/.e2e-testing.env" 2>/dev/null \
             | tail -1 | cut -d= -f2- | tr -d '"' | xargs)
fi
EXECUTOR="${E2E_EXECUTOR:-$EXECUTOR}"
EXECUTOR="${EXECUTOR:-cloud}"
echo "E2E_EXECUTOR=$EXECUTOR"
```

Capture the printed value. Acceptable: `cloud`, `local`. Anything else → reply `RUN_FAILED invalid E2E_EXECUTOR=<value> (expected: cloud|local)` and stop.

### 2. Dispatch

Invoke the matching subagent via a single `Task` call. Pass:

- `subagent_type`:
  - `e2e-runner` if `EXECUTOR=cloud`
  - `e2e-runner-local` if `EXECUTOR=local`
- `description`: `Run E2E checklist (cloud)` or `Run E2E checklist (local)`
- `prompt`: a single string in this exact shape:
  ```
  ARGS: $ARGUMENTS
  CWD:  <current working directory, absolute>
  ```

### 3. Forward the reply verbatim

When the subagent returns, print its final reply to the user **verbatim**. Do not summarize, paraphrase, or annotate. If the subagent's reply starts with `RUN_FAILED`, `RUN_DRY`, or any other contract token, still forward it verbatim — those tokens are intentional.

## Why a dispatcher

- **Single entry point** — users learn one command, not two.
- **Per-project switch** — drop `E2E_EXECUTOR=local` in `.e2e-testing.env` and every run on that project is local; teammates without LM Studio fall back to cloud automatically.
- **No magic** — the dispatcher prints the resolved value before delegating, so it's clear which path ran.
