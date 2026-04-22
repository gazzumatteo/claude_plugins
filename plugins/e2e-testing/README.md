# e2e-testing

End-to-end testing workflow for markdown checklists: **create** them from code, **validate** them against changes, and **execute** them via Playwright browser automation.

## The three commands

| Command | What it does |
|---|---|
| `/create-checklist <target>` | Analyze the codebase and produce a new checklist file in one of the 4 supported shapes |
| `/validate-checklist <path>` | Audit an existing checklist against current code + project memory, ask followups, update in place |
| `/run-checklist <path>` | Execute every step via Playwright + CLI, capture evidence, produce a structured report |

All three produce or consume the same markdown format, parsed by `scripts/parse_checklist.py`.

## `/create-checklist`

```
/create-checklist <feature-or-path-or-description> [--shape table|prose|nested|cli] [--out <path>] [--fast]
```

Orchestrates two subagents:
- **`checklist-architect`** — scans the repo (routes, APIs, auth, destructive ops) and outputs a JSON skeleton of scenarios
- **`checklist-writer`** — turns the skeleton into markdown in the requested shape and validates the output parses cleanly

The result is a file ready for `/run-checklist`. The orchestrator batches clarifying questions (base URL, credentials, shape) in one `AskUserQuestion` round — no per-step nagging.

Examples:
```
/create-checklist src/routes/admin
/create-checklist "user onboarding flow" --shape table
/create-checklist "data pipeline smoke tests" --shape cli --out tests/TEST_E2E_PIPELINE.md
```

## `/validate-checklist`

```
/validate-checklist <path-to-checklist.md> [--dry-run] [--since <git-ref>] [--no-memory]
```

Orchestrates two subagents:
- **`checklist-auditor`** — reads the checklist, the `git log`/`diff` since the file was last touched, and `claude-mem` memory if available. Produces an audit JSON with `obsolete[]`, `missing[]`, `ambiguous[]`, `prereq_issues[]`
- **`checklist-updater`** — applies an approved change-set while preserving the file's shape (table stays table, etc.) and re-verifies with the parser

The orchestrator asks followups via `AskUserQuestion` for ambiguous items before anything is written. `--dry-run` produces the audit report without modifying the file.

Examples:
```
/validate-checklist docs/TEST_E2E_CHECKLIST.md
/validate-checklist docs/TEST_E2E_CHECKLIST.md --dry-run
/validate-checklist docs/TEST_E2E_CHECKLIST.md --since v0.4.0
/validate-checklist docs/TEST_E2E_CHECKLIST.md --no-memory   # skip claude-mem lookup
```

## `/run-checklist`

```
/run-checklist <path-to-checklist.md> [--fast] [--dry-run] [--only ids] [--from N]
```

Executes the checklist. The plugin:

1. **Parses** the file into a normalized list of steps
2. **Asks** you for missing URLs / credentials in one batched question
3. **Executes** each step using Playwright (real browser) and CLI commands (curl, docker, gh, npm)
4. **Captures evidence** — screenshots, HTTP responses, console logs — for every step
5. **Produces** a report (markdown + JSON) under `.e2e-runs/` next to your checklist
6. **Asks** you (HITL) when steps are ambiguous, credentials are missing, or actions are destructive
7. **Audits** its own work — PASS without evidence gets downgraded, silently-skipped steps fail the run

## What it does NOT do

- Does not write code to fix bugs (reports them for a stronger model to fix later)
- Does not skip tests for convenience
- Does not modify your source test file during a run (enforced by PreToolUse hook + tool allowlist)
- Does not invent URLs, credentials, or API paths during creation/validation

## Supported checklist shapes

| Shape | Example header |
|---|---|
| Table | `\| # \| Step \| Azione \| Risultato Atteso \| Pass \|` |
| Prose | H2/H3 sections with bash code blocks |
| Nested | `- [ ]` bullets under headings |
| CLI-only | Prose without UI steps |

Italian and English column headers both work.

## Per-project configuration (optional)

Create `.e2e-testing.yml` in your project root or next to the checklist:

```yaml
base_url: https://staging.example.com
browser: chromium
headed: true
credentials_file: ./docs/TESTING_CREDENTIALS.md
pre_run:
  - docker compose up -d
post_run:
  - docker compose logs > .e2e-runs/last-logs.txt
auto_confirm_destructive: false
```

## Output

Report files are written to `<checklist_dir>/.e2e-runs/<basename>.<YYYY-MM-DD-HHMM>.{md,json}` with an `evidence/` subdirectory containing screenshots and HTTP logs.

## Agents shipped with this plugin

| Agent | Role |
|---|---|
| `checklist-architect` | Scans code, proposes scenarios → JSON skeleton |
| `checklist-writer` | Skeleton → markdown in the chosen shape |
| `checklist-auditor` | Diff + memory → JSON audit (obsolete/missing/ambiguous) |
| `checklist-updater` | Applies approved change-set, preserves shape |
| `test-executor` | Runs each step via Playwright/Bash, captures evidence, reports bugs |

## Requirements

- Playwright MCP plugin installed (for `/run-checklist`) — e.g. [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) or the version packaged in [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official)
- Python 3.10+
- Optional: `claude-mem` MCP plugin — enables project-memory context during `/validate-checklist`
