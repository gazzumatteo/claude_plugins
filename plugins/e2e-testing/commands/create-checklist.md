---
description: Create a new E2E test checklist (markdown) by analyzing the project and emitting a file ready for /run-checklist
argument-hint: <feature-or-path-or-description> [--shape table|prose|nested|cli] [--out <path>] [--fast]
allowed-tools: Read, Bash, AskUserQuestion, Task, Glob, Grep
model: sonnet
---

# Create E2E checklist

You orchestrate the creation of a new E2E test checklist. You do NOT design or write the file yourself — that is the job of two specialized subagents: `checklist-architect` (designs scenarios from code) and `checklist-writer` (emits the markdown in one of the supported shapes).

The file you produce MUST parse cleanly with `${CLAUDE_PLUGIN_ROOT}/scripts/parse_checklist.py` so it can be executed later with `/run-checklist`.

## Arguments

`$ARGUMENTS` contains: a feature description, a path to a file/folder to cover, or a free-text brief — plus optional flags:

- `--shape <table|prose|nested|cli>` → force the output shape. If omitted, the architect suggests a shape based on what it finds.
- `--out <path>` → where to write the checklist. Default: `docs/TEST_E2E_CHECKLIST.md` at the repo root (create the folder if missing).
- `--fast` → pass Haiku signal to both subagents.

## Steps you execute in order

### 1. Clarify the target

Parse `$ARGUMENTS`. Classify it:

- **Path argument** (file/folder exists) → the target is existing code; the architect reads it.
- **Feature name** (no path, short text) → ambiguous; need clarification.
- **Free-text brief** (long text) → treat as spec; the architect still cross-references the repo.

If the target is ambiguous, use `AskUserQuestion` ONCE with up to 4 batched questions:

- "What feature or flow should the checklist cover?" (options: extracted guesses from repo scan + Other)
- "What is the base URL for the environment under test?" (options: values found in `.e2e-testing.yml` / `.env*` / README + Other + "skip, ask later")
- "Are credentials needed?" (options: yes — path to a credentials file, no, later)
- "Preferred shape?" (options: table, prose, nested, cli, "let the architect decide")

Do not ask more than one batched round. If the user says "decide", pass that through and let the subagents choose.

### 2. Check for per-project config

Use `Glob` to find `.e2e-testing.yml` at the repo root or near the target. If present, `Read` it and note `base_url`, `credentials_file`, `browser`. These feed into the writer so the checklist's prereqs match the execution env.

### 3. Delegate scenario design to `checklist-architect`

Use the `Task` tool with `subagent_type: checklist-architect`. Pass in the prompt:

- The clarified target (path or description)
- The repo root (absolute)
- Any `base_url` / `credentials_file` / `browser` from the config
- The user's answers from step 1
- The requested shape (or "auto")
- A strict output contract: the architect MUST return a JSON skeleton (see the architect's own spec for the exact shape)

Save the returned JSON to `/tmp/e2e-skeleton-<timestamp>.json`.

### 4. Sanity-check the skeleton

Read the JSON. Verify:

- `shape` is one of `table|prose|nested|cli`
- `steps[]` is non-empty and each step has `id`, `section`, `action`, `expected`
- `prereqs[]` exists (may be empty)
- No step action is a literal TODO / placeholder

If any check fails, re-invoke the architect ONCE with the specific problem noted. If it still fails, stop and report the skeleton path so the user can inspect.

### 5. Delegate file authoring to `checklist-writer`

Use the `Task` tool with `subagent_type: checklist-writer`. Pass:

- Absolute path to the skeleton JSON
- Absolute path of the output file (from `--out`, or default — resolve to absolute)
- The shape decided in step 4 (the architect may have overridden "auto")
- The `base_url` / `credentials_file` / `browser` values for prereqs
- A reminder that the file MUST parse as that shape — the writer calls the parser itself to verify

The writer returns the final path and a parse summary.

### 6. Verify with the parser

Run:

```
${CLAUDE_PLUGIN_ROOT}/scripts/parse_checklist.py <output-path>
```

Read stdout. Confirm:

- `shape` matches what was requested
- `step_count` equals the number of steps in the skeleton (or architect's note explains any merges)
- `steps[]` ids are unique

If the parser returns `shape=unknown` or `step_count=0`, invoke the writer ONCE more with the parser output as feedback. If it still fails, stop and report.

### 7. Present summary to the user

Print:

```
## Checklist created

Output:  <absolute path>
Shape:   <shape>
Steps:   <N>  (destructive: <M>)
Title:   <title>
Prereqs: <count>

Next:
  /run-checklist <path>              # execute it
  /validate-checklist <path>         # re-check later after code changes
```

Do NOT auto-run. The user decides when to execute.

## Error handling

- Parser check fails twice → stop, keep the file as-is, show the parser error
- Architect returns empty `steps[]` → stop; the target was probably too vague — suggest the user rerun with a narrower scope
- User rejects all clarification options → stop politely

## Never do

- Do not invent URLs, credentials, or API paths not visible in the repo or user answers — placeholders like `<YOUR_URL>` are OK, fabricated prod URLs are not
- Do not write steps for hypothetical future features
- Do not auto-run the checklist after creation
- Do not overwrite an existing file without asking — if `--out` points to an existing file, `AskUserQuestion` whether to overwrite, append, or pick a new path
