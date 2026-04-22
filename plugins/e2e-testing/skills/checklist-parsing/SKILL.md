---
name: checklist-parsing
description: Reference for parsing E2E test checklists written in markdown. Covers 4 supported shapes (table, prose, nested, cli-only) with Italian and English column headers. Load this skill when the deterministic parser emits shape=unknown or when disambiguating a step that doesn't classify cleanly.
---

# Checklist parsing reference

The `e2e-testing` plugin's deterministic parser (`scripts/parse_checklist.py`) classifies test checklists into 4 shapes. When it returns `shape=unknown`, or when a step's classification is ambiguous, use this reference.

## Shape 1 — Table

**Markers**: at least 3 markdown tables, at least one with a `Pass` / `Status` / `Stato` column.

**Canonical column headers** (Italian / English):
- `#` — step ID (e.g. `1.1`, `14.5`)
- `Step` / `Fase` — phase/section (optional, often implicit from `##` heading)
- `Azione` / `Action` — what the tester does
- `Risultato Atteso` / `Expected` / `Result` — what should happen
- `Pass` / `Status` / `Stato` — checkbox with `[ ]` / `[x]` / `[-]` / `[~]`

**Markers in Pass column**:
- `[ ]` = pending (initial)
- `[x]` = passed in previous run (re-run anyway; final report is source of truth)
- `[-]` = skipped/not-applicable
- `[~]` = blocked

**Example** (skills-maker `TEST_E2E_CHECKLIST_v3.md`):
```markdown
| # | Step | Azione | Risultato Atteso | Pass |
|---|------|--------|------------------|------|
| 3.1 | Health check API | `GET /api/health` | JSON with version, status: "ok" | [x] |
```

## Shape 2 — Prose

**Markers**: H2/H3 sections, optional bash/sh code blocks, no Pass column.

Each `##` or `###` heading that is not a meta heading (Prerequisites, Summary, Legend, Context, Notes) becomes a step. The heading is the action, the body is the expected/instructions. Fenced `bash`/`sh` blocks inside become `cli_commands`.

**Example** (nql-acm `TESTING_GUIDE.md`):
```markdown
### 1. Direct Testing (Without API)

Test individual components:
```bash
python test_qdrant_integration.py
```
```

## Shape 3 — Nested

**Markers**: many `- [ ]` bullet lines (threshold: ≥5 across the file, or ≥10 when tables are also present).

Each checkbox line becomes a step. The nearest preceding `##`/`###` heading is the section. Checkbox marker determines initial status (same semantics as table Pass column).

**Example** (NQL-PM, WAS):
```markdown
### 🔍 Schema Integrity
- [ ] All required tables exist in PostgreSQL
- [ ] All required collections exist in MongoDB
```

Nested wins over table when checkboxes dominate (ratio `checkbox_hits >= 3 * table_hits`) — a file with many checkboxes is a checklist even if it includes one summary table.

## Shape 4 — CLI-only

**Markers**: same structure as Prose but CLI keyword density is twice the browser keyword density.

Treated like Prose but with `needs_browser=false` default. Steps are typically command-line validations (pytest, docker, curl) without UI interaction.

**Example** (WAS validation commands section):
```markdown
### Validation Commands

```bash
python tests/validation/run_all_validations.py
python tests/validation/test_schema_integrity.py
```
```

## Meta-section detection

These H2/H3 headings are NEVER treated as steps (case-insensitive, IT/EN):

- `Prerequisiti` / `Prerequisites`
- `Context` / `Contesto`
- `Riepilogo` / `Summary`
- `Note` / `Notes`
- `Legenda` / `Legend`
- `Credenziali` / `Credentials`

Prereq content is extracted separately into the `prereqs[]` field.

## Destructive step detection

A step is flagged `destructive: true` if its action or expected text contains (case-insensitive):

`elimin*`, `delet*`, `drop`, `wipe`, `reset`, `purge`, `truncate`, `remove`, `rimuov*`, `cancel*` (but not `cancellato`), `clear`, `flush`

**False positives are expected** (e.g. "verify data NOT eliminated" will flag). The orchestrator handles this with HITL — the user confirms before proceeding with a flagged step.

## When the parser returns `shape=unknown`

Fall back manually:
1. Count H2/H3 headings — if ≥3, treat as Prose
2. Look for fenced code blocks — if many, likely CLI
3. Ask the user to specify the shape, or to restructure the file

Never invent structure not present in the file. If the file is genuinely unstructured (one giant prose blob), return a single "execute the whole file as described" super-step and let HITL handle the rest.

## Italian / English column mapping

| IT | EN |
|---|---|
| Azione | Action, Step |
| Risultato Atteso | Expected, Result |
| Pass | Pass, Status |
| Prerequisiti | Prerequisites |
| Credenziali | Credentials |
| Fase | Phase |

The parser's column matching is case-insensitive and accepts either language.
