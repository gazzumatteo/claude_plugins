# Changelog

All notable changes to this marketplace are documented here.

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
