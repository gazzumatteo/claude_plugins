---
description: Run health diagnostics for mobile-testing — verifies xcrun, adb, Appium, and connected devices
argument-hint: [--verbose]
allowed-tools: Task
---

# Mobile Health Check (dispatcher)

## What you do (and only this)

1. Invoke the `mobile-setup` subagent via `Task` with `prompt: DOCTOR`. No additional arguments needed.

2. When the subagent returns, print its final reply to the user **verbatim**.
