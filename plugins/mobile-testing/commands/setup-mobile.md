---
description: Bootstrap the mobile-testing MCP server — install dependencies, create Android AVD, register MCP config
argument-hint: [--skip-appium] [--skip-android] [--backend native|appium|both]
allowed-tools: Task
---

# Setup Mobile MCP (dispatcher)

## What you do (and only this)

1. Invoke the `mobile-setup` subagent via `Task`. Pass:
   - `subagent_type`: `mobile-setup`
   - `description`: `Setup mobile MCP`
   - `prompt`: a single string in this exact shape:
     ```
     ARGS: $ARGUMENTS
     PLUGIN_ROOT: ${CLAUDE_PLUGIN_ROOT}
     ```

2. When the subagent returns, print its final reply to the user **verbatim**.
