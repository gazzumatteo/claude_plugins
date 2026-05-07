---
description: Run a mobile-testing checklist. Auto-routes to the cloud (Claude) or local (LM Studio) executor based on $MOBILE_EXECUTOR.
argument-hint: --checklist FILE.md --device-id UDID [--out-dir DIR]
allowed-tools: Bash, Task
---

# Mobile runner — dispatcher (cloud or local, auto-routed)

This is the single entry point for running a mobile checklist. It picks the executor based on `MOBILE_EXECUTOR`, then delegates to the matching subagent. To force a specific executor regardless of env, use `/mobile-runner-local` (force local) — there's no force-cloud variant because cloud is the default.

## What you do (and only this)

### 1. Resolve the executor

Use `Bash` ONCE to read `executor:` from `.testing.yml` in the project root. Process env `MOBILE_EXECUTOR` overrides:

```bash
EXECUTOR=""
if [ -f "${PWD}/.testing.yml" ]; then
  EXECUTOR=$(awk -F': *' '/^executor:/ {gsub(/["'\'' ]/, "", $2); print $2; exit}' \
             "${PWD}/.testing.yml" 2>/dev/null)
fi
EXECUTOR="${MOBILE_EXECUTOR:-$EXECUTOR}"
EXECUTOR="${EXECUTOR:-cloud}"
echo "MOBILE_EXECUTOR=$EXECUTOR"
```

Capture the printed value. Acceptable: `cloud`, `local`. Anything else → reply `RUN_FAILED invalid MOBILE_EXECUTOR=<value> (expected: cloud|local)` and stop.

### 2. Dispatch

Invoke the matching subagent via a single `Task` call. Pass:

- `subagent_type`:
  - `mobile-runner` if `EXECUTOR=cloud`
  - `mobile-runner-local` if `EXECUTOR=local`
- `description`: `Run mobile checklist (cloud)` or `Run mobile checklist (local)`
- `prompt`: a single string in this exact shape:
  ```
  ARGS: $ARGUMENTS
  PLUGIN_ROOT: ${CLAUDE_PLUGIN_ROOT}
  PROJECT_ROOT: ${PWD}
  ```

### 3. Forward the reply verbatim

When the subagent returns, print its final reply to the user **verbatim**. Do not summarize, paraphrase, or annotate. If the subagent's reply starts with `RUN_FAILED` or any other contract token, still forward it verbatim — those tokens are intentional.

## Why a dispatcher

- **Single entry point** — users learn one command, not two.
- **Per-project switch** — set `executor: local` in `.testing.yml` and every run on that project is local; teammates without LM Studio can override with `MOBILE_EXECUTOR=cloud`.
- **No magic** — the dispatcher prints the resolved value before delegating, so it's clear which path ran.
