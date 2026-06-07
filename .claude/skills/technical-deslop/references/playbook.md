# Technical Deslop Playbook

Execution order for a behavior-preserving, subtractive cleanup pass. Edit **changed hunks only**.

## 1. Capture diff scope

- Staged: `git diff --cached`
- Branch delta (mirrors CI): `git diff $(git merge-base origin/main HEAD)..HEAD`
  - Base ref is `origin/main` — the only real PR target. CI computes its diff the same way (`git diff origin/main...HEAD`).
- If `FILES` (or explicit user targets) is provided, restrict work to those paths.
- Work the added/changed hunks. Do not open unrelated files or reformat untouched code.

## 2. Establish local style baseline

- Read nearby unchanged code in the same module — match its error-handling, logging, and naming idioms.
- Read repo conventions: `CLAUDE.md`, `pyproject.toml` `[tool.ruff]` (extend-select `I`; ignore `E402,F401,F811`; isort `combine-as-imports`, `force-sort-within-sections`), and `.github/workflows/pr-validate.yml`.
- Identify idioms to preserve before deleting anything.

## 3. Remove slop patterns

- Apply `slop-patterns.md`. Prefer **deletion** over replacement where safe; otherwise **collapse to original intent**.
- Highest-confidence removals are ones `ruff` would otherwise flag: unused locals (F841), redefinitions (F811), unused imports (F401), empty f-strings (F541). CI ignores these (legacy rot), but removing them is pure behavior-preserving slop removal toward a `ruff check`-clean diff.
- Do **not** broaden scope into refactors, renames, or unrelated formatting churn — that is clean-code's job.

## 4. Re-check behavioral boundaries

- No business-logic changes unless explicitly requested.
- Preserve validated security/permission checks and input validation.
- Preserve intentional metrics/tracing hooks (logfire/langsmith spans, structured logging).
- Preserve error handling required by a subsystem contract (maps/raises domain errors, retries, releases resources, returns a documented fallback).
- When uncertain whether a pattern is intentional, **keep it** (or ask) — do not change behavior.

## 5. Lint-clean floor (do not regress the gate)

- Keep the diff `py_compile`-clean.
- Keep it clean under the CI command: `ruff check --select E9,F --ignore E402,F401,F541,F811,F841 --no-cache <changed .py>`.
- Never create an `E9` or `F821` (undefined name): do not delete the last use of an import or a still-referenced name.
- If you remove an import, leave imports in sorted, combined, sectioned order so isort (`I`) stays clean — don't hand-reorder.
- Don't introduce an empty f-string (F541) when collapsing a log/format string.
- Default to 88-column formatting on touched lines. Leave no `.bak`/`.orig`/`.swp` artifacts.

## 6. Final review pass

- Compare with surrounding style and remove outliers introduced by generation.
- Confirm no accidental API/contract changes and no widened public signatures.
- Provide a concise 1-3 sentence cleanup summary (what was removed/collapsed, and where).
