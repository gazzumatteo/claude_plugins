# Changelog

All notable changes to this marketplace are documented here.

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
