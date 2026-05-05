# Changelog

All notable changes to this marketplace are documented here.

## [0.5.5] — 2026-05-05

### Fixed (mobile-testing v0.1.3)
- **MCP server now auto-registers on plugin install.** Previous releases relied on `/setup-mobile` to write into `~/.claude/mcp.json`, but Claude Code reads MCP servers from `~/.claude.json`, not from `~/.claude/mcp.json` — so the tools never appeared in `/mcp`. The plugin now ships a `.mcp.json` in its root, which Claude Code picks up automatically when the plugin is installed/enabled. After install + `/setup-mobile` (for `uv sync`), restart Claude Code once and `mcp__mobile-testing__list_devices` etc. become available.
- `/setup-mobile` no longer touches `~/.claude.json` / `~/.claude/mcp.json`. README and agent updated to remove the manual MCP-registration step.

## [0.5.4] — 2026-05-05

### Changed (mobile-testing v0.1.2)
- **Renamed internal package and MCP server** to drop the `dap-` project-internal prefix. Python package `dap_mobile_mcp` → `mobile_mcp`; pyproject distribution `dap-mobile-mcp` → `mobile-mcp`; MCP server name `dap-mobile-mcp` → `mobile-testing` (matches the plugin). Tools now appear as `mcp__mobile-testing__list_devices`, etc.
- Updated all imports, README, agent setup instructions, doctor.sh banner, and SKILL.md accordingly.

## [0.5.3] — 2026-05-05

### Fixed (mobile-testing v0.1.1) — pre-flight code review
- **Native router** — `MOBILE_BACKEND=native` previously fell back to iOS-only and Android devices never appeared in `list_devices`. Introduced `NativeBackendRouter` that aggregates iOS + Android devices and dispatches every per-device call to the right sub-backend by platform (cached after first lookup).
- **Backend lifecycle** — every MCP tool call used to instantiate a fresh backend and `await backend.shutdown()` in `finally`. For Appium that destroyed the WebDriver session immediately after `start_bridge`, breaking every subsequent `tap`/`swipe` with `RuntimeError: No Appium session`. Backend is now a module-level singleton with an `atexit` shutdown hook.
- **Android screencap** — `_adb` ran with `text=True`, mangling PNG bytes via UTF-8 decoding before they were re-encoded with `latin1`. Added a binary-safe `_adb_bytes` and switched `get_screenshot` / `save_screenshot` to it.
- **iOS swipe osascript fallback** — referenced `step_delay` which only existed in the Quartz code path, so `pyobjc` missing meant `UnboundLocalError` instead of a graceful fallback. Refactored into `_quartz_swipe` (returns `True`/`False`) and a clean osascript fallback.
- **iOS tap scaling** — taps used `(window_origin + device_pixels)` ignoring the Simulator's window↔device scale factor, so on retina simulators every tap landed at the wrong spot. Now reads device pixel dimensions from `xcrun simctl io ... enumerate --json` and applies `scale = window_size / device_size` per axis.
- **iOS stop_bridge** — used to call `simctl shutdown` unconditionally, killing simulators the user had opened manually. Now tracks `_booted_by_us` and only shuts down what `start_bridge` actually booted.
- **Android start_bridge** — used to claim `already_running` for any `emulator-*` id without verifying. Now checks `adb devices`; if the id matches an AVD name (`emulator -list-avds`), spawns the emulator and waits up to 120s for it to come online; otherwise raises a clear error. `stop_bridge` only kills emulators it started.
- **Stub MCP tools removed** — `test_get_active`, `test_list_projects`, `test_run` were registered as `@mcp.tool()` but only returned `{"status":"stub"}`. Removed from the surface.
- **Real assertions on Appium** — `assert_exists` / `assert_not_exists` / `assert_count` now run a real `find_element` query through Appium and produce `status: "ok"` or `status: "failed"` with the matched count. On native backend they record `status: "skipped"` with a clear note instead of silently passing.
- **`pyobjc-framework-Quartz`** moved to `; sys_platform == 'darwin'` so Linux installs no longer fail at `uv sync`.
- **DSL parser** — extended `bounds_hint` to accept `"x,y wxh"` (legacy MobAI) in addition to `"x+y+w+h"`; `double_tap` and `long_press` now accept a `predicate` fallback like `tap`; `assert_screen_changed` returns `skipped` instead of fake `ok`. Directional swipe heuristics use device-pixel coords (configurable via `center_x` / `center_y`) so backend scaling stays consistent.
- **Cleanups** — duplicate `uiautomator dump` call in Android `observe`, dead `_adb_json`, redundant iOS runtime-string parser, broken `../LICENSE` link in plugin README.

## [0.5.2] — 2026-05-04

### Added (mobile-testing)
- **`mobile-testing` plugin** — direct iOS Simulator and Android Emulator control via MCP, no MobAI desktop app, no rate limits. Pluggable backends: native (xcrun simctl + adb + Quartz/osascript) and Appium (XCUITest + UIAutomator2, auto-started on-demand). Drop-in replacement for MobAI MCP: same 14 tool names (`list_devices`, `execute_dsl`, `get_screenshot`, `install_app`, ...), same DSL v0.2 format.
- Two slash commands: `/setup-mobile` (bootstrap Appium + Python deps + Android AVD + MCP config) and `/mobile-doctor` (health check).
- iOS touch via Quartz CGEvent + Simulator window position detection (no Appium needed for basic tap/swipe/type).
- Android fully supported via adb: tap, swipe, type, keyevent, uiautomator dump for UI tree.
- `MOBILE_BACKEND` env var switches backends at runtime (`native` or `appium`).

## [0.5.1] — 2026-05-01

### Fixed (e2e-testing)
- **Critical: `report.json` now survives SIGINT / Bash-window timeout / hard kill.** Real-world test of a 331-step authenticated checklist hit the 10-minute slash-command Bash window at step 1.17 and lost everything because the final report was only written on clean exit. The runner now flushes `report.json` atomically (via `report.json.tmp` → rename) after **every step**, with a `status` field set to `in_progress` mid-run and switched to `complete` / `interrupted` / `crashed` in the top-level `try/finally`. `KeyboardInterrupt` is caught explicitly and the partial report carries `fatal_error` describing the cause.
- The runner's `step_definition.txt` evidence file is now written when a step tagged as CLI has no commands the parser could extract — the user immediately sees the raw action+expected text and a hint explaining which fence tags the parser scans (`bash`, `sh`, `shell`, `zsh`, no-tag) so they can re-tag misclassified blocks.

### Added (e2e-testing)
- **`--only IDS` / `--from ID` / `--to ID`** flags on the runner. Comparison is dotted-tuple aware (`1.10 > 1.9`). Skipped steps are recorded as `status="skipped"` with a clear reason — they still appear in the report so post-run tooling has full coverage. Together with the incremental write these enable a clean resume workflow on long checklists.
- The `e2e-runner-local` agent now warns the user once via `AskUserQuestion` when `step_count > 50` and no scoping flag was given, offering three paths: run anyway (relying on `--from <last-id>` to resume after timeout), pick a scope now, or run detached from the terminal entirely.
- Agent now flags unrecognized arguments (e.g. `target=...` typos) instead of silently dropping them.

## [0.5.0] — 2026-05-01

### Added (e2e-testing)
- **CLI executor in `/run-checklist-local`** — pure-CLI steps (`needs_cli=True && !needs_browser`) are no longer skipped. The runner shells out each command (120s timeout, chain stops on first non-zero exit), captures stdout/stderr/exit_code per command, and asks the local model in a single text-only turn whether the transcript matches the expected outcome. Evidence: `cli_results.json` + `transcript.txt` per step.
- **Lazy Chromium launch** — checklists that contain only CLI steps no longer spin up the browser; a 100% CLI suite runs in seconds.
- **Loop guard** — when the same non-`finish_step` tool call repeats 3 times consecutively with identical arguments, the runner auto-fails the step with a clear note instead of burning the whole `--max-iterations` budget.

### Fixed (e2e-testing)
- **Critical: runner no longer crashes mid-run.** A `Page.screenshot` timeout in real-world tests previously aborted the whole process with no `report.json`. Now: `BrowserTools.take_screenshot()` catches `playwright.TimeoutError` and any `Exception`, returns `None`, and `run_step` falls back to a text-only message inviting the model to use `accessibility_snapshot`. Default screenshot timeout reduced from 30s to 10s — hung pages fail fast.
- **`report.json` is always written** — even when a step blows up unexpectedly, the executing thread crashes outright, or the Playwright context fails to close. Each step iteration is now wrapped in `try/except` that converts an unhandled exception into `StepResult(status="error", error=traceback, ...)` and writes a `crash.txt` evidence file. A top-level crash sets `fatal_error` in the report so post-run tooling can distinguish "everything failed cleanly" from "the runner aborted".
- LM Studio / Nemotron-3-Nano-Omni does not honor `tool_choice="required"`. The CLI executor now uses `tool_choice="auto"` with a content-based verdict fallback for servers that emit prose instead of a function call.

### Notes (e2e-testing)
- Authentication / credential injection is still NOT implemented — Phase 5 target. Checklists with login walls will fail at the auth step regardless of how robust the runner is.
- Mixed browser+CLI steps still run as browser-only (the bundled `cli_commands` are not auto-executed inside a browser turn).

## [0.4.0] — 2026-05-01

### Added (e2e-testing)
- **`/run-checklist-local`** — companion to `/run-checklist` that offloads the browser-automation loop to a local OpenAI-compatible model (LM Studio, vLLM, …). Zero Claude tokens for screenshots and tool calls. Same checklist format, same final-reply shape; falls back gracefully when the local endpoint is unreachable.
- New agent `e2e-runner-local` — verifies endpoint, dispatches the runner, summarizes the report.
- New script `scripts/e2e_local_runner.py` — Playwright sync runner with vision + tool-calling agent loop, per-step context reset, latest-image-only history trimming, JSONL trace + screenshot per step, structured `report.json`.
- New diagnostic `scripts/spike/spike_capability_check.py` — rerun whenever the model or LM Studio version changes; prints `GO / NO-GO` for reachability, vision grounding, and combined vision + tool calling.
- Configuration cascade for `LMSTUDIO_*` env: shell env → `${CWD}/.e2e-testing.env` → `${XDG_CONFIG_HOME}/claude-e2e-testing/config.env` → plugin-dev fallback. Survives plugin updates and is shareable via dotfiles.
- `--check-config` flag on the runner: diagnoses which cascade level provided each value, exits 0/2 for ok/incomplete.
- Default evidence dir is now `<checklist-dir>/.e2e-runs/<timestamp>/` — matches the convention used by `/run-checklist`.

### Notes (e2e-testing)
- The local executor does NOT yet read credentials nor execute CLI-only steps — those steps are skipped with an explicit reason. Use `/run-checklist` (Claude-driven) for protected pages or CLI-heavy checklists. Local executor is best for long browser-only suites you iterate on frequently.
- The `e2e-runner` Claude-driven path is unchanged and remains the default.

## [0.3.0] — 2026-04-22

### Added
- `code-optimizer` plugin — scan, plan, apply, and verify code optimizations across 11 categories (deduplication, type consolidation, dead-code, circular deps, type strengthening, error handling, AI-slop removal, cognitive complexity, magic constants, naming inconsistency, excessive parameters). Multi-language (TS/JS, Python, generic) with static-tool integration (knip, madge, jscpd, vulture, radon) where available.
- Four commands: `/optimize:scan` (read-only audit), `/optimize:plan` (prioritized checklist), `/optimize:apply` (batch apply with regression guard + rollback), `/optimize:verify` (final baseline diff).
- Regression-safety net: captures lint/typecheck/test/build baseline before any change, re-runs after every batch, rolls back via `git reset --hard` on any regression. Never commits without explicit user authorization, never uses `--no-verify`.

## [0.2.0] — 2026-04-22

### Changed
- **Renamed plugin** `e2e-browser-testing` → `e2e-testing` (scope now covers the full authoring + validation + execution lifecycle, not just browser runs). Invocation prefix changes from `/e2e-browser-testing:…` to `/e2e-testing:…`.
- **Renamed per-project config file** `.e2e-browser-testing.yml` → `.e2e-testing.yml`. If you had the old filename in a repo, rename it.

### Added
- `/create-checklist` command — generate a new E2E checklist from a feature / path / description. Orchestrates two specialized subagents (`checklist-architect` designs scenarios from code, `checklist-writer` emits markdown in one of 4 supported shapes). Validates the output with the parser before returning.
- `/validate-checklist` command — audit an existing checklist against current code and project memory, ask followups, update the file while preserving its shape. Orchestrates two specialized subagents (`checklist-auditor` produces the audit JSON, `checklist-updater` applies the approved change-set). Uses `claude-mem` MCP for project context when available.

## [0.1.0] — 2026-04-17

### Added
- `e2e-browser-testing` plugin — execute E2E test checklists via Playwright browser automation
