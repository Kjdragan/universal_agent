---
name: technical-deslop
description: "Behavior-preserving removal of AI-generated noise (slop) from a Python diff — staged changes plus the current branch's delta against origin/main — leaving the change set ruff-clean. Use when the user says \"remove AI slop\", \"deslop this\", \"clean up this diff\", \"strip redundant comments\", \"remove the echo docstrings\", \"kill the over-defensive try/except\", \"tidy the PR before merge\", \"final cleanup pass\", \"de-noise my changes\", \"remove narration logging\", or wants automated-generation output normalized before review. Scope is CHANGED HUNKS ONLY; it deletes/collapses noise the model added and never changes logic, renames symbols, restructures control flow, or touches lines outside the diff. For generative refactoring (renaming, splitting functions, fixing smells) use clean-code instead."
user-invocable: true
risk: safe
source: "Adapted from vincentkoc/dotskills (MIT) — technical-deslop"
---

# Technical Deslop

Remove AI-style noise (slop) the model added to a change set, while preserving behavior, error contracts, security checks, and observability. Operate on the **diff only** — not the whole file or codebase.

## When to use

- A diff feels over-explained, over-defensive, or inconsistent with surrounding code.
- You want a final cleanup pass before review/PR/merge.
- You want to normalize style after automated generation, leaving the change set `ruff`-clean.

## When NOT to use

- **Generative refactoring** — renaming for clarity, splitting long functions, fixing code smells, restructuring logic. That changes shape and is the job of the **clean-code** skill.
- Editing untouched code or files outside the current change set.
- Any change that alters behavior, public API/contract, control flow, or removes a security/validation/observability hook.

If a cleanup would require renaming a variable, extracting a function, or changing control flow, it is out of scope — hand it to **clean-code**. Deslop's only verbs are **delete** and **collapse-to-original-intent**.

## Boundary vs. clean-code

- **clean-code** is *generative refactoring*: it makes code better by restructuring it (renaming, splitting functions, fixing smells) across the whole file/codebase, regardless of git state.
- **technical-deslop** is *behavior-preserving, subtractive de-noising of a DIFF*: it deletes slop the model added within the current change set. It never renames, never restructures logic, never touches lines outside the diff, and produces no semantic change.

## Workflow

1. **Capture scope** (changed hunks only):
   - Staged: `git diff --cached`
   - Branch delta: `git diff $(git merge-base origin/main HEAD)..HEAD`
   - If the user gives a file scope (`FILES`) or explicit targets, restrict to those paths.
2. **Establish local style baseline** — read nearby unchanged code in the same module and repo conventions (`CLAUDE.md`, `pyproject.toml` `[tool.ruff]`, lint configs) to identify idioms to preserve (error-handling, logging, naming).
3. **Apply removals** using `references/slop-patterns.md` (Python rubric: high/medium confidence + KEEP list), in the execution order in `references/playbook.md`. Edit only changed hunks; prefer deletion over replacement.
4. **Hold the behavioral boundary** — no logic changes unless the user explicitly asked. Preserve validated security/permission checks and intentional metrics/tracing hooks. If unsure whether a pattern is intentional, keep it (or ask).
5. **Keep the diff lint-clean** — every edit must leave the change set `py_compile`-clean and pass the CI floor `ruff check --select E9,F --ignore E402,F401,F541,F811,F841` plus isort (`I`) ordering. Never make an edit that would newly trigger `F821` (undefined name) by deleting the last use of an import or name. Default to 88-column formatting on any line you touch; leave no `.bak`/`.orig`/`.swp`.
6. **Report** a concise 1-3 sentence summary of what was de-slopped and where.

## Inputs

- Optional file scope (`FILES`) or explicit user file targets.
- Current branch diff and staged diff.
- Repository conventions (`CLAUDE.md`, `pyproject.toml`, lint/CI config).

## Outputs

- Behavior-preserving, subtractive cleanup confined to touched hunks.
- A change set that stays `py_compile`- and `ruff`-clean.
- A 1-3 sentence summary of what was normalized and where.

## References

- `references/playbook.md` — execution order for a behavior-preserving pass.
- `references/slop-patterns.md` — Python-flavored detection rubric (high/medium confidence + KEEP list) aligned to UA's ruff gate.
