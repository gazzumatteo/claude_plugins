# Changelog

All notable changes to this marketplace are documented here.

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
