---
name: optimization-playbook
description: Reference playbook for code-optimizer scanners and executors. Defines 11 optimization categories with detection criteria, red flags (what NOT to touch), safe refactor recipes, and regression indicators. Load when scanning a category or when applying a fix and you need to check "is this actually safe to do here?".
---

# Code optimization playbook

This is the single source of truth for all 11 optimization categories handled by the `code-optimizer` plugin. Every scanner and the executor reference their own section before acting.

## How to use this playbook

- **Scanners** consult their section to decide what is a real finding vs. a false positive, and to set `confidence` + `requires_manual_review` flags.
- **The executor** consults the section to follow the safe refactor recipe and to know which regression indicators matter beyond the project's test suite.
- Anything ambiguous defaults to `requires_manual_review: true`. The scanner is never the last line of defense — the user is.

## The Finding schema (output contract for every scanner)

```json
{
  "id": "<CATEGORY>-<NNN>",
  "category": "deduplication|type-consolidation|dead-code|circular-deps|type-strengthening|error-handling|slop-removal|complexity|magic-constants|naming|excessive-parameters",
  "severity": "high|medium|low",
  "confidence": "high|medium|low",
  "files": ["relative/path.ts:START-END", "..."],
  "description": "What the scanner observed, in one sentence.",
  "proposed_action": "What the executor should do if approved.",
  "risk": "low|medium|high",
  "requires_manual_review": true,
  "reason_for_manual_review": "Why a human must confirm.",
  "tool_evidence": {"jscpd": "...", "knip": "...", "...": "..."}
}
```

Severity = how much noise/drift the issue causes. Risk = how easily a fix can regress. A finding can be high severity + high risk (worth fixing, careful). Confidence = how sure the scanner is the finding is real.

---

## 1. DEDUPLICATION

Repeated logic, copy-pasted functions, redundant abstractions.

**Detection criteria**
- jscpd duplicate blocks ≥ 30 tokens, ≥ 2 occurrences, across different files.
- Functions with near-identical AST (same control flow, same calls, same returns) even if named differently.
- Helper modules/files that re-implement something already present in a shared util (grep for the utility name in the new file, check if it's imported or re-declared).

**Red flags — do NOT consolidate**
- Two functions with the same body but different domains (e.g. `UserValidator.isEmail` vs `ContactValidator.isEmail`) that belong to different bounded contexts. They may diverge tomorrow — consolidating creates coupling across domains.
- Code that looks identical but is in different layers (controller-layer validation vs domain-layer validation) — they exist for a reason.
- Test fixtures or mocks that duplicate production shapes — duplication here is intentional (tests pin the contract).
- Language/framework boilerplate (React hooks with same scaffolding, Express handlers with the same response shape) — these only *look* alike; the logic inside diverges.

**Safe refactor recipe**
1. Locate all occurrences. Note the call sites.
2. Check for hidden differences: return types, exception cases, side effects.
3. Extract to a neutral module: `src/shared/<name>.ts` (not inside one of the call sites).
4. Replace every call site in a single batch.
5. Run lint, typecheck, tests. If any fail → rollback.

**Regression indicators beyond tests/lint**
- Behavior drift when the "same" logic was actually slightly different in one call site.
- Increased import depth creating new circular deps — re-run cycle-mapper after.

---

## 2. TYPE CONSOLIDATION

Types defined in multiple places that should be one.

**Detection criteria**
- TypeScript `interface` / `type` declarations with identical or near-identical shape across files.
- Python `TypedDict` / `dataclass` / Pydantic models with overlapping fields.
- Divergence indicator: one copy has an extra/missing optional field → drift in progress.

**Red flags — do NOT consolidate**
- A DB model and its DTO are intentionally separate even if identical today.
- Request / response / domain entity triples — consolidating loses boundary information.
- Types in a public package API vs types inside the package — public types are frozen by contract.

**Safe refactor recipe**
1. Define the canonical type in a shared module (`src/shared/types/<name>.ts` or `app/schemas/<name>.py`).
2. Re-export the canonical type from each old location as a transitional shim.
3. Migrate imports in one batch.
4. Delete the shims after a full run passes.
5. Run typecheck after each step.

**Regression indicators**
- Serialization drift (a consumer expected the old optional-field shape).
- JSON schema / OpenAPI generation output diff → check committed schemas.

---

## 3. DEAD CODE REMOVAL

Unused exports, unreferenced functions, orphaned files.

**Detection criteria**
- knip output: `files`, `exports`, `dependencies`, `devDependencies`.
- vulture output for Python: unused functions, variables, imports.
- Cross-reference grep: the symbol appears nowhere else in the codebase.

**Red flags — do NOT remove without manual review**
- Dynamic imports: `import(\`./handlers/${name}\`)`, `importlib.import_module(...)`.
- String-referenced routes: Next.js `pages/`, `app/`, Remix routes, Rails routes.
- Framework conventions: React hooks starting with `use`, Django admin registrations, Flask blueprints, decorators with `register=True`.
- Code generation: Prisma generated files, GraphQL codegen output, protobuf/grpc stubs — they look unused until a build regenerates.
- Public API: anything exported by a library's `index.ts` / `__init__.py` — it may be imported by external consumers.
- Test utilities referenced only from other test files → confirm they have zero callers even across all test directories.
- CLI entry points registered in `package.json` `bin`, `pyproject.toml` `[project.scripts]`.
- Reflection / metaprogramming: `getattr(module, name)` with computed name.

**Every dead-code finding defaults to `requires_manual_review: true`.** This is the category with the highest rollback rate if you move too fast.

**Safe refactor recipe**
1. Confirm zero callers with a widened grep (include dotfiles, include build configs, include markdown).
2. Remove in a single small batch (one symbol or one file at a time).
3. Run the full build, not just tests — build errors often surface hidden imports.
4. If a framework is involved, also run the framework's dev server/build step.

**Regression indicators**
- Build step (Webpack/Vite/Next build) surfaces broken imports.
- Runtime error at dynamic-import sites — these won't show at compile time.

---

## 4. CIRCULAR DEPENDENCIES

Import cycles between modules.

**Detection criteria**
- madge `--circular` output (TS/JS).
- Python equivalent: `pydeps --reverse` or manual import tracing.
- Prioritize: cycles that cross bounded-context boundaries > cycles within one module > trivial two-file cycles.

**Red flags — do NOT introduce**
- New abstraction layers whose only reason to exist is "break the cycle". These usually re-introduce the cycle via the new layer.
- Lazy-loading / deferred imports as a workaround — they hide the cycle instead of resolving it.

**Safe refactor recipe**
1. Identify the shared logic causing the cycle. It's usually a type, constant, or utility.
2. Extract it to a neutral module that neither side imports from the other.
3. Re-point both sides to the neutral module.
4. Re-run madge — the cycle should be gone, no new cycles introduced.
5. If only one side uses the shared logic, consider moving it fully to that side instead.

**Regression indicators**
- New cycles introduced on other paths (check full madge output).
- Performance: breaking a cycle sometimes forces re-evaluation order changes in module-scope side effects.

---

## 5. TYPE STRENGTHENING

`any`, `unknown`, untyped parameters, weak return types.

**Detection criteria**
- TypeScript: grep for `: any`, `as any`, `any[]`, `Record<string, any>`, function parameters with no annotation, implicit-any emitted by `--noImplicitAny` lint mode.
- Python: missing type hints, `Any`, `Dict[str, Any]`, untyped kwargs.
- Return types inferred as `unknown` because an internal cast uses `any`.

**Red flags — `unknown` may be legitimate**
- Input from `JSON.parse` / `YAML.parse` / network responses before validation — `unknown` is correct; validation should narrow.
- Third-party library boundaries where no types exist.
- Generic utilities explicitly typed to accept anything (e.g. a logger).

**Safe refactor recipe**
1. Start from leaf types (constants, enums, DTOs) and work outward.
2. Replace `any` with the discovered concrete type. For "I don't know yet" cases, use `unknown` and narrow at the boundary.
3. After each batch: run typecheck. If new errors appear, they're probably real bugs the `any` was hiding — surface them, don't silence with new `any`.
4. Never broaden a type to make a test pass — that's covering a bug.

**Regression indicators**
- New compilation errors are usually *good* (they reveal pre-existing bugs). Only rollback if the change introduces runtime regressions the tests catch.

---

## 6. ERROR HANDLING CLEANUP

Try/catch blocks that swallow errors or mask failures.

**Detection criteria**
- Empty `catch` blocks: `catch (e) {}`, `except: pass`.
- Catch blocks that only log (`console.error(e)`) and proceed as if nothing happened, with no fallback semantics.
- Catch blocks that return a default value (`return null`, `return []`) hiding actual failures from callers.
- Broad catches: `catch (Exception)`, `catch (...)`, `except BaseException`.

**Red flags — keep the handler**
- Cleanup handlers: closing a file/connection/stream regardless of outcome.
- Explicit recovery: retrying with exponential backoff, fallback to cache, degraded-mode response with user-visible error.
- User-facing error reporting: catch → transform to user-friendly message → re-throw or return error object.
- At a process boundary: top-level handlers in CLI tools / HTTP middleware that log + return 500.

**Safe refactor recipe**
1. For swallowing catches: remove the try/catch entirely and let the error propagate. Add to the caller's contract if needed.
2. For "catch + log + default": replace the default with re-throw, OR make the default explicit by documenting it (typed `Result<T, E>` return, explicit `.orElse(...)`).
3. For broad catches that should be narrow: restrict to the specific error type.
4. Never remove a handler that closes a resource in `finally`.

**Regression indicators**
- Tests that were silently passing due to swallowed errors now fail — these failures are *correct*; the test was lying.
- New unhandled-rejection warnings → track them to their new (correct) source.

---

## 7. SLOP / DEPRECATED CODE REMOVAL

AI-generated artifacts, edit-history comments, deprecated paths.

**Detection criteria**
- Comments that narrate edits: `// added by ...`, `// was X now Y`, `// TODO: refactor`, `// NOTE: keeping for compat`.
- Placeholder logic: `throw new Error("not implemented")`, `pass  # TODO`, stub returns of empty objects/arrays in production code.
- Functions/classes annotated `@deprecated` with zero call sites (check grep for the name).
- Fallback code paths for versions / APIs / platforms no longer supported.
- Multiple "v2"/"new"/"legacy" files for the same concept when only one is wired in.
- Comments that explain *what* the code does without any *why* (e.g. `// return the user` above `return user;`).
- Commented-out code blocks (dead code pretending to be documentation).

**Red flags — keep**
- Deprecation markers on public APIs with active external consumers (check usage telemetry / release notes, don't assume zero callers = safe).
- Compatibility shims explicitly dated / tied to a version policy ("remove after 2027-01").
- Comments explaining non-obvious WHY: invariants, race conditions, hidden constraints, bug workarounds with an issue link.
- Stubs in test fixtures or interfaces intentionally left for implementers.

**Safe refactor recipe**
1. Remove comments first — lowest risk.
2. Remove `@deprecated` symbols after confirming zero callers via grep (including string references).
3. Remove fallback paths only after confirming the "non-fallback" path is always taken (check config, env, version gates).
4. Rewrite comments worth keeping so a new engineer understands WHY the code is there, not what it does.

**Regression indicators**
- Removing a fallback path breaks behavior in legacy environments → check if the target environments still matter.

---

## 8. COGNITIVE COMPLEXITY & LONG FUNCTIONS

Hard-to-read functions, deep nesting, tangled conditionals.

**Detection criteria**
- `eslint-plugin-sonarjs` `cognitive-complexity` rule → threshold default 15.
- Function length > 50 lines (excluding comments/blank).
- Nesting depth > 4.
- Cyclomatic complexity (radon for Python, eslint-complexity for JS) > 10.

**Red flags — keep as-is**
- Performance-critical hot loops where inlining logic is the point.
- Generated code (parser tables, state machines).
- Legitimate state machines where the linear switch/if chain *is* the clearest form.

**Safe refactor recipe**
1. Identify the cohesive sub-steps. Each sub-step is a candidate function.
2. Extract with descriptive names. Prefer pure helpers over side-effecty helpers.
3. Early returns > nested ifs.
4. Pull guard clauses to the top.
5. Name boolean expressions via intermediate variables rather than inlining compound conditions.
6. Run tests after each extraction. Don't batch 5 extractions before testing — you won't know which one broke.

**Regression indicators**
- Extracted helper captures a variable from the outer scope that changes between iterations → classic closure bug.
- Changed short-circuit evaluation order (e.g. `a && b()` vs extracted function that always calls `b()`).

---

## 9. MAGIC NUMBERS / MAGIC STRINGS

Hardcoded literals scattered in the code.

**Detection criteria**
- Numeric literals other than `0, 1, -1, 2` in non-test code, especially when used more than once.
- Quoted strings > 3 characters used more than once, especially as function arguments (URLs, keys, status values).
- Regex literals used in multiple places with the same pattern.

**Red flags — keep inline**
- Mathematical constants in a formula where the name adds no clarity (`const TAU = 2 * Math.PI` may be clearer than inlining, but `const TWO = 2` is noise).
- Values used exactly once in a place where the name would just rename the literal.
- Test data — fixtures benefit from literal values for at-a-glance readability.
- Framework-expected strings where the framework controls the name (`"display: none"` in CSS-in-JS).

**Safe refactor recipe**
1. Group constants by semantic cluster into a named enum / const object / module.
2. For strings that appear in multiple places: extract to a constants module.
3. For magic numbers representing units (minutes, bytes) make the unit explicit: `const SESSION_TIMEOUT_MS = 30 * 60 * 1000`.
4. Run tests to ensure extraction didn't change numeric precision (rare but happens with large numbers in JS).

**Regression indicators**
- String extraction broke a case-sensitive comparison (extracted constant has different casing than call site).

---

## 10. NAMING INCONSISTENCY

Same concept named differently across the codebase.

**Detection criteria**
- Same entity with varying names: `user` / `usr` / `u`, `customer` / `client`, `order` / `purchase`.
- Verb prefix drift for similar operations: `getUser` / `fetchUser` / `loadUser` / `retrieveUser`.
- Boolean naming drift: `isActive` / `active` / `enabled` (for the same flag).
- Casing inconsistency for the same identifier across files (`userId` vs `UserId` vs `user_id` — can be legit at language boundaries, but not within one language).

**Red flags — keep inconsistent**
- Domain terms that legitimately differ (a `User` in auth is not a `User` in billing — renaming forces incorrect coupling).
- External API contracts that dictate the name (don't rename a field that serializes to JSON consumed externally).
- Historical names preserved for stability (public API, database columns, feature flags).

**Safe refactor recipe**
1. Pick the canonical name (survey frequency; the majority usage usually wins).
2. Use IDE-level rename to update references (safer than find-replace).
3. Rename in a dedicated commit — do not mix with other changes.
4. Run full typecheck + tests.
5. Grep for the old name in strings, SQL, JSON, config — those aren't caught by rename refactorings.

**Regression indicators**
- Database column / API field named the same as the code identifier was renamed → serialization broken.
- Log search queries / dashboards referencing old name → tooling breaks silently.

---

## 11. EXCESSIVE PARAMETERS / PARAMETER OBJECTS

Functions with too many positional arguments or boolean flags.

**Detection criteria**
- Functions with > 4 positional parameters.
- Functions with > 2 boolean flag parameters (classic "flag argument" code smell).
- Constructors accepting a flat list where half the args are optional.

**Red flags — keep positional**
- Simple coordinate-like calls (`Point(x, y, z)`) where naming would add noise.
- Framework / library signatures you can't change (`fetch(url, options)`).
- Functions with clear, stable signatures where changing them would ripple through many call sites with no gain.

**Safe refactor recipe**
1. For > 4 params → introduce a parameter object type, migrate call sites.
2. For > 2 booleans → extract an options object with named fields.
3. Prefer named arguments / destructured parameters at the call site.
4. Run typecheck after each migration batch.

**Regression indicators**
- Default values dropped in the refactor → a call site that relied on defaults now gets `undefined`.
- Argument order swap during refactor (same-typed params accidentally flipped).

---

## Universal rules (apply to every category)

1. **No regression is acceptable.** The full baseline (lint + typecheck + test + build) must either stay identical or improve. Any degradation → rollback the batch.
2. **Never batch across categories.** A single `/optimize:apply` run works on one category at a time so that if something breaks you know exactly what to blame.
3. **Never commit or push unless the user asked.** Stage only.
4. **Never use `--no-verify` or skip pre-commit hooks.**
5. **Never introduce new tools or dependencies to the target project.** The static tools used by the scanners run from the plugin, not from `npm install` in the user's project.
6. **Default to `requires_manual_review: true` when unsure.** A slow review cycle is cheaper than an incorrect automated fix.
