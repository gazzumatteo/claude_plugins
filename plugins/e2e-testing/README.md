# e2e-testing

End-to-end testing workflow for markdown checklists: **create** them from code, **validate** them against changes, and **execute** them via Playwright browser automation.

## The three commands

| Command | What it does |
|---|---|
| `/create-checklist <target>` | Analyze the codebase and produce a new checklist file in one of the 4 supported shapes |
| `/validate-checklist <path>` | Audit an existing checklist against current code + project memory, ask followups, update in place |
| `/run-checklist <path>` | Execute every step via Playwright + CLI (Claude-driven), capture evidence, produce a structured report |
| `/run-checklist-local <path>` | Same as above, but the browser-automation loop runs against a **local** vision-capable model (LM Studio). Zero Claude tokens for screenshots and tool calls. Falls back gracefully if the local endpoint is missing. |

All four produce or consume the same markdown format, parsed by `scripts/parse_checklist.py`.

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

## `/run-checklist-local` (zero-token alternative)

```
/run-checklist-local <path-to-checklist.md> [--dry-run] [--headed] [--allow-destructive] [--max-iterations N]
```

Same input format as `/run-checklist` and (almost) same output, but execution happens on your machine: a Python script (`scripts/e2e_local_runner.py`) drives Playwright Chromium under the control of a local OpenAI-compatible model. Useful when you have a capable local model on hand and want to keep test runs out of your Claude budget.

**Trade-offs vs `/run-checklist`:**

| | `/run-checklist` (Claude) | `/run-checklist-local` (LM Studio) |
|---|---|---|
| Browser automation cost | Claude tokens (screenshots dominate) | Free (local inference) |
| Required setup | Playwright MCP plugin | Local model + Chromium + `.env.local` |
| Credentials | HITL with secrets pulled from `credentials_file` | **Not yet supported** — protected pages will fail |
| CLI step support | Yes (curl, docker, gh, npm, …) | Yes for pure-CLI steps (subprocess + single-turn LM verdict). Mixed browser+CLI steps still run as browser-only — the auto-execution of bundled CLI commands inside a browser step is a Phase 5 item. |
| Shape support | Table / Prose / Nested / CLI | Same, parsed by the same `parse_checklist.py` |
| Recovery from tool errors | Yes (Claude reasons over failures) | Yes (the local model retries; loop guarded by `--max-iterations`) |
| HITL clarification mid-run | Yes | No — the runner is autonomous, end-of-run only |

**Setup (one-time):**

1. Install a vision + tool-calling model in LM Studio. Tested with `nvidia/nemotron-3-nano-omni` (Apple Silicon MLX build, ~32B, 200K context).
2. Enable "Serve on Local Network" in LM Studio so the runner can reach it.
3. Configure the endpoint in **one** of the three locations below (cascade explained next).
4. Install Playwright Chromium: `uv run --with playwright python -m playwright install chromium` (the slash command will offer to do this if missing).
5. (Optional) Validate everything with the capability check:
   ```bash
   uv run plugins/e2e-testing/scripts/spike/spike_capability_check.py
   ```
   It prints a `GO / NO-GO` verdict on reachability, vision grounding, and tool calling.

**Configuration cascade (where to put the endpoint):**

The runner looks for `LMSTUDIO_BASE_URL`, `LMSTUDIO_MODEL`, and `LMSTUDIO_API_KEY` in this order — **lower lines override higher lines**, except shell env which always wins:

| # | Location | When to use |
|---|---|---|
| 1 | `${CLAUDE_PLUGIN_ROOT}/scripts/.env.local` (plugin-dev fallback) | Only when developing this plugin in-tree. Don't rely on it for marketplace installs — the directory may be wiped on update. |
| 2 | `${XDG_CONFIG_HOME:-$HOME/.config}/claude-e2e-testing/config.env` (user-global) | One endpoint shared across all your projects. Persists across plugin updates. Sync via dotfiles. |
| 3 | `${CWD}/.e2e-testing.env` (per-project) — **recommended** | Different endpoints per project (e.g. staging vs prod model). Add the file to your project's `.gitignore`. |
| 4 | Process env (`export LMSTUDIO_BASE_URL=...`) | direnv / `.envrc` / `mise` users. Wins over every file. |

**Examples — pick ONE:**

Per-project (the common case): create `.e2e-testing.env` in your project root —

```bash
# .e2e-testing.env  (gitignore me!)
LMSTUDIO_BASE_URL=http://192.168.1.101:1234/v1
LMSTUDIO_MODEL=nvidia/nemotron-3-nano-omni
LMSTUDIO_API_KEY=lm-studio
```

Then add to your project's `.gitignore`:

```
.e2e-testing.env
.e2e-runs/
```

User-global (set once, reuse everywhere):

```bash
mkdir -p ~/.config/claude-e2e-testing
cat > ~/.config/claude-e2e-testing/config.env <<'EOF'
LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
LMSTUDIO_MODEL=nvidia/nemotron-3-nano-omni
LMSTUDIO_API_KEY=lm-studio
EOF
chmod 0600 ~/.config/claude-e2e-testing/config.env
```

direnv / `.envrc`:

```bash
# .envrc  (in your project root, then `direnv allow`)
export LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
export LMSTUDIO_MODEL=nvidia/nemotron-3-nano-omni
```

**Verifying setup:**

```bash
uv run plugins/e2e-testing/scripts/e2e_local_runner.py --check-config --project-root .
```

The output lists every cascade level (`loaded` / `not found`), the resolved values, and finishes with `config_status=ok` (rc=0) or `config_status=incomplete` (rc=2) plus a hint of where to put the file. The `/run-checklist-local` slash command runs the same check during step 3.

When invoked from `/run-checklist-local`, the agent stops with a HITL prompt (offering to write the config file for you) if the check returns `incomplete` — no surprises. When invoked directly via `uv run`, the runner falls back to localhost defaults, which is convenient when LM Studio is on the same machine as the runner.

**Output:**

- `scripts/runs/<timestamp>/report.json` — structured summary (steps, status, bugs, durations)
- `scripts/runs/<timestamp>/step-<id>/screenshot.png` — last viewport screenshot per step
- `scripts/runs/<timestamp>/step-<id>/trace.jsonl` — per-iteration tool calls + results

The `runs/` directory is gitignored.

**When to use which:**

- Big checklist (>20 steps), iterating frequently → local
- Critical run where you want Claude's reasoning on ambiguous outcomes → cloud
- Pre-merge smoke on staging with credentials → cloud (until local supports auth)
- CI-style unattended pass on a public preview → local with `--allow-destructive` off

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
| `test-executor` | Runs each step via Playwright/Bash (Claude-driven), captures evidence, reports bugs |
| `e2e-runner-local` | Verifies the local endpoint, dispatches `e2e_local_runner.py`, returns the same final-reply shape as `e2e-runner` |

## Requirements

- Playwright MCP plugin installed (for `/run-checklist`) — e.g. [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) or the version packaged in [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official)
- Python 3.10+ with [`uv`](https://docs.astral.sh/uv/) (the local runner uses PEP 723 inline metadata)
- Optional: `claude-mem` MCP plugin — enables project-memory context during `/validate-checklist`
- Optional (only for `/run-checklist-local`): a local OpenAI-compatible endpoint serving a vision + tool-calling model, plus Playwright Chromium installed via `playwright install chromium`
