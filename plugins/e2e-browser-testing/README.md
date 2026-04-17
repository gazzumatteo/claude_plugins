# e2e-browser-testing

Execute E2E test checklists from markdown files via browser automation.

## What it does

You write a markdown test checklist (tables, prose, or nested checklists). You run `/run-checklist <file>`. The plugin:

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
- Does not modify your source test file (enforced by PreToolUse hook + tool allowlist)

## Supported checklist shapes

| Shape | Example header |
|---|---|
| Table | `\| # \| Step \| Azione \| Risultato Atteso \| Pass \|` |
| Prose | H2/H3 sections with bash code blocks |
| Nested | `- [ ]` bullets under headings |
| CLI-only | Prose without UI steps |

Italian and English column headers both work.

## Usage

```
/run-checklist docs/TEST_E2E_CHECKLIST.md
/run-checklist docs/TEST_E2E_CHECKLIST.md --fast              # Haiku (faster, cheaper)
/run-checklist docs/TEST_E2E_CHECKLIST.md --only 3.1,3.2      # single/subset
/run-checklist docs/TEST_E2E_CHECKLIST.md --from 5            # resume from step 5
/run-checklist docs/TEST_E2E_CHECKLIST.md --dry-run           # parse + validate, do not run
```

## Per-project configuration (optional)

Create `.e2e-browser-testing.yml` in your project root or next to the checklist:

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

## Requirements

- [Playwright MCP](https://github.com/anthropics/claude-plugins-official) plugin installed
- Python 3.10+
