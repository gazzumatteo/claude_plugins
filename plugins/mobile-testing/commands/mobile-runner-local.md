---
description: Run a mobile-testing checklist via the local LM Studio executor (zero Claude tokens for device automation)
argument-hint: --checklist FILE.md --device-id UDID [--out-dir DIR]
allowed-tools: Task
---

# Mobile Runner (local) — dispatcher

## What you do (and only this)

1. Invoke the `mobile-runner-local` subagent via `Task`. Pass:
   - `subagent_type`: `mobile-runner-local`
   - `description`: `Run mobile checklist via local LM Studio`
   - `prompt`: a single string in this exact shape:
     ```
     ARGS: $ARGUMENTS
     PLUGIN_ROOT: ${CLAUDE_PLUGIN_ROOT}
     PROJECT_ROOT: ${PWD}
     ```

2. When the subagent returns, print its final reply to the user **verbatim**.
