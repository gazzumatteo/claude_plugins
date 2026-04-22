# Gazzumatteo Claude Plugins

A Claude Code plugin marketplace focused on pragmatic, production-ready workflows: end-to-end test automation and regression-safe code optimization.

Maintained by [Matteo Gazzurelli](https://github.com/gazzumatteo) — CTO, full-stack + mobile + AI. Plugins built to be used daily on real codebases, not demos.

- **Marketplace name**: `gazzumatteo-claude-plugins`
- **Current version**: see `.claude-plugin/marketplace.json`
- **License**: see [LICENSE](./LICENSE)

## Plugins at a glance

| Plugin | Category | What it does |
|---|---|---|
| [**e2e-testing**](./plugins/e2e-testing) | testing | Create, validate, and execute E2E markdown test checklists via Playwright. Structured markdown + JSON reports with evidence (screenshots, HTTP logs). |
| [**code-optimizer**](./plugins/code-optimizer) | code-quality | Scan → plan → apply → verify code optimizations across 11 categories with a baseline+rollback regression safety net. Multi-language (TS/JS, Python, generic). |

## Install

Add the marketplace to Claude Code:

```bash
claude plugin marketplace add git@github.com:gazzumatteo/claude_plugins.git
```

Install the plugins you want:

```bash
claude plugin install e2e-testing@gazzumatteo-claude-plugins
claude plugin install code-optimizer@gazzumatteo-claude-plugins
```

Uninstall with `claude plugin uninstall <name>@gazzumatteo-claude-plugins`.

## Plugin details

### `e2e-testing` — end-to-end testing lifecycle

Three commands cover the full E2E testing lifecycle over markdown checklists.

| Command | Purpose |
|---|---|
| `/create-checklist <target>` | Analyze the codebase and emit a checklist in one of 4 shapes (table, prose, nested, cli) |
| `/validate-checklist <path>` | Audit an existing checklist against current code + project memory, update in place |
| `/run-checklist <path>` | Execute every step via Playwright + CLI, capture evidence, produce a structured report |

**Highlights**:
- 4 supported checklist shapes with a deterministic parser (`parse_checklist.py`), IT/EN column headers.
- Evidence-first: every `PASS` requires a file on disk (screenshot, HTTP response, log). Passes without evidence are auto-downgraded by a post-run audit.
- Human-in-the-loop: asks for missing URLs / credentials / destructive confirmations in one batched round, never per step.
- Structured bug records with severity, reproduction steps, environment — ready for a stronger model to fix later.
- Optional `.e2e-testing.yml` for base URL, browser, credentials file, pre/post run hooks.

**Requires**: [Playwright MCP plugin](https://github.com/anthropics/claude-plugins-official).

Full docs: [plugins/e2e-testing/README.md](./plugins/e2e-testing/README.md).

### `code-optimizer` — regression-safe code optimization

Four commands cover the audit → plan → apply → verify loop across 11 optimization categories.

| Command | Purpose |
|---|---|
| `/optimize:scan` | Read-only audit → `findings.json` + scan report |
| `/optimize:plan` | User-approved, prioritized checklist |
| `/optimize:apply` | Apply findings in batches with regression guard + rollback |
| `/optimize:verify` | Final verdict against the recorded baseline |

**The 11 categories**:

1. **deduplication** — repeated logic, without merging lookalikes from different domains
2. **type-consolidation** — scattered types with silent drift
3. **dead-code** — unused files/exports/deps, manually verified against dynamic imports and framework conventions
4. **circular-deps** — import cycles mapped + extracted to neutral modules
5. **type-strengthening** — `any` / `unknown` placeholders → concrete types
6. **error-handling** — try/catch blocks that swallow or mask failures
7. **slop-removal** — AI artifacts, edit-history comments, deprecated paths
8. **complexity** — long functions, deep nesting, high cognitive complexity
9. **magic-constants** — hardcoded numbers/strings → named constants
10. **naming** — same concept with different names across the codebase
11. **excessive-parameters** — fat signatures → parameter objects

**Regression safety net**:
- Captures a **baseline** (lint + typecheck + test + build) before any modification.
- Applies findings **one batch at a time** (default size 1), saving a persistent pre-batch snapshot as a git ref (`refs/code-optimizer/batch-<id>`) that won't be GC'd.
- After the executor returns, verifies the diff is contained within the finding's declared files. Scope violation → batch aborted.
- Re-runs the baseline and diffs. Any regression (new errors, failed tests, worse exit codes) → **rollback**: `git reset --hard` the snapshot ref + targeted `rm` of any file the executor newly created (never a bare `git clean` — your unrelated untracked work stays safe).
- Blocked items are marked `[~]` in the checklist; the user decides whether to continue.
- **Never** commits or pushes without explicit authorization. **Never** uses `--no-verify`. **Never** broadens a change beyond the finding's declared files.

**Ecosystem support** (static tools are opt-in — detected, never installed):

| Ecosystem | Tools leveraged when available |
|---|---|
| TypeScript / JavaScript | knip, madge, jscpd, eslint-plugin-sonarjs |
| Python | vulture, radon, ruff, pyright/mypy |
| Go | go vet, go build, go test (baseline only) |
| Other (Rust, Dart, PHP, Ruby, ...) | Claude semantic analysis fallback |

**Typical workflow**:

```bash
/optimize:scan                                    # audit
/optimize:plan                                    # pick categories, build checklist
/optimize:apply --category slop-removal           # start with lowest-risk category
/optimize:apply --category magic-constants
/optimize:apply --category dead-code --force-risky --dry-run   # preview risky items
/optimize:apply --category dead-code --force-risky             # then apply
/optimize:verify                                  # final baseline diff
```

Optional `.code-optimizer.yml` controls languages, enabled categories, project commands, path filters, batch size, and commit strategy.

Full docs: [plugins/code-optimizer/README.md](./plugins/code-optimizer/README.md).

## Requirements

- **Claude Code CLI** (required for all plugins)
- **Python 3.10+** (embedded parsing / aggregator scripts)
- **git** (required by `code-optimizer` for snapshot and rollback)
- **Playwright MCP plugin** (required only for `e2e-testing`)
- Optional: the target project's own tool stack (npm / pnpm / yarn / uv / poetry / pip / go / cargo) — plugins detect and use whatever the project already has, they never install anything.

## Repository layout

```
claude_plugins/
├── .claude-plugin/
│   └── marketplace.json         # the marketplace manifest
├── plugins/
│   ├── e2e-testing/             # E2E testing plugin
│   └── code-optimizer/          # code optimization plugin
├── CHANGELOG.md                 # marketplace-wide release notes
├── README.md                    # this file
└── LICENSE
```

Each plugin is self-contained under `plugins/<name>/` with its own `.claude-plugin/plugin.json`, commands, agents, skills, hooks, and scripts. See each plugin's README for its internal structure.

## Design principles

All plugins in this marketplace follow a consistent pattern:

- **Commands orchestrate, agents work.** Slash commands never do heavy lifting — they delegate to focused subagents via the `Task` tool. Each agent has a single, explicit job and an explicit tool allowlist (no wildcards).
- **Structured outputs.** JSON and markdown produced by the plugin parses cleanly with a deterministic script — never with `grep`/`sed` heuristics at use time.
- **Explicit safety rails.** Regression guards, evidence requirements, HITL for destructive actions. No feature bypasses hooks, no feature auto-commits.
- **Detect, don't install.** Plugins check what the target project already has and fall back gracefully. They never run `npm install`, `pip install`, `uv add`, etc. on the user's project.
- **Multilingual friendly.** Both Italian and English labels are accepted where relevant (e.g. checklist column headers `Azione` / `Action`).

## Contributing

Contributions and feedback are welcome. For new plugins:

1. Create `plugins/<name>/.claude-plugin/plugin.json` with `name`, `version`, `description`, `author`.
2. Add your commands (`plugins/<name>/commands/*.md`), agents (`plugins/<name>/agents/*.md`), optional skills, hooks, scripts.
3. Register the plugin in `.claude-plugin/marketplace.json`.
4. Bump `marketplace.json` version and add a `CHANGELOG.md` entry.
5. Open a PR.

For bugs and feature requests, open an issue on GitHub with:
- The plugin name and version.
- What you ran (command + arguments).
- Expected vs observed behavior.
- Relevant output from `.e2e-runs/` or `.optimize/` if applicable.

## Links

- Maintainer: [Matteo Gazzurelli](https://github.com/gazzumatteo) · matteo@duckma.com
- Release history: [CHANGELOG.md](./CHANGELOG.md)
- License: [LICENSE](./LICENSE)
- Claude Code docs: [code.claude.com/docs](https://code.claude.com/docs)
