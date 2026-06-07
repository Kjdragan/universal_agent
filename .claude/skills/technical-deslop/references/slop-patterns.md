# Slop Patterns (Python)

Detection rubric for AI-generated noise in a Python diff. This codebase is async Python 3.12+ (claude-agent-sdk, anthropic, fastapi, pydantic, logfire/langsmith, composio). Every removal must be behavior-preserving, confined to changed hunks, and leave the diff `ruff`-clean.

## High-confidence removals

1. **Redundant comments restating code** — `# increment counter` over `counter += 1`; `# loop over items`. Noise/redundant comment class.
2. **Docstrings echoing the signature** — `"""Get the user. Args: user_id. Returns the user."""` adding nothing beyond the typed signature. Collapse to a one-line intent, or remove if the name already says it (keep public-API docstrings — see KEEP).
3. **Over-broad `try/except` that swallow** — `except Exception: pass`, `except Exception: return None`, or log-and-continue around code with no real failure mode, added defensively by the model. Remove the wrapper only when it changes nothing; never remove one that is load-bearing for an error contract.
4. **Unnecessary `# type: ignore` / `cast()`** — added to silence a checker where the types already line up; redundant `cast(X, x)` where `x` is already `X`.
5. **Defensive `None`-checks that can't trigger** — `if x is not None:` on a value the type/control flow guarantees non-None (right after construction, on a non-Optional param, or after Pydantic validation).
6. **Verbose obvious logging / narration** — `logger.info("Starting function foo")`, `logger.debug("Entering loop")`, `print("done")` step-by-step narration. Distinct from real structured observability (see KEEP).
7. **Needless intermediate variables** — `result = compute(); return result` → `return compute()`; single-use temps that only restate the expression. (Truly unused = `ruff` F841, highest confidence.)
8. **Dead generation scaffolding** — unused imports (F401), redefinitions (F811), empty f-strings `f""` (F541), commented-out "old version" blocks, `# noqa` with no remaining violation, leftover `# TODO: implement` on already-implemented code.
9. **Restating-the-obvious section banners** — `# ===== HELPER FUNCTIONS =====`, `# Main logic below` (position-marker smell).

## Medium-confidence removals

- Logging that repeats a value already returned or raised on the same path.
- Generic helper functions used exactly once when inline is the local norm — *delete only if collapsing is purely subtractive*; extracting/renaming is clean-code's job.
- Redundant `None`/empty guards after a validated parsing layer (e.g. after a Pydantic model already enforced the field).

## KEEP — never remove (verbose-looking but load-bearing)

1. **Real error contracts** — `try/except` that maps/raises a domain error, retries, releases a resource, or returns a documented fallback; any handler that does real work or preserves an API guarantee.
2. **Structured logging / observability** — `logfire`/`langsmith` spans, `logger.*` with structured fields/context, trace instrumentation, metrics. Observability is load-bearing here (Logfire setup ordering even drives the `E402` ignore). Don't mistake telemetry for narration.
3. **Security / input validation** — `defusedxml`, auth/JWT checks, path/host allowlists, sanitization before shell/SQL/network calls, and the surgical `# noqa: F821` defensive `globals()` patterns the gate intentionally preserves.
4. **Public-API docstrings** — module/class/public-function docstrings (consumed by mkdocs-material), param semantics not obvious from types, units, side effects, raised exceptions, and non-obvious "why" comments (legal, intent-of-regex, external-library quirks).
5. **Intentional-pattern markers the repo relies on** — `# Removed YYYY-MM-DD: …` rationale comments and load-bearing `# noqa` annotations documented in `pyproject.toml`/CI. Don't strip documented decisions.
6. **Type annotations themselves**, and `cast`/`# type: ignore` that are actually required for the checker to pass. Only the *redundant* ones are slop.

## Python type-escape-hatch guidance (generalized from TS `as any`)

- Replace a redundant `cast(Any, x)` / blanket `# type: ignore` only when the types already align — prefer precise hints, `isinstance` narrowing, `TypedDict`/`Protocol`/`dataclass`, or existing project Pydantic models / type aliases over ad-hoc `Any` / `dict[str, Any]`.
- Prefer the project's existing utility types and `Protocol`s over ad-hoc escape hatches — but introducing new ones is structural work (clean-code), not deslop.
- Do **not** widen public/exported signatures or return types for convenience (e.g. narrowing a return to `Any`, or `Optional`-widening a param). Deslop never loosens a contract.
- The over-abstracted-naming smell (`process_data_handler_manager`, needless manager/handler/factory layering) is a **flag, not a deslop action** — renaming is clean-code's job.

## Ruff alignment (leave the diff cleaner, never noisier)

- Highest-confidence slop = what `ruff` would flag and CI tolerates only as legacy rot: F841 (unused local), F811 (redefinition), F401 (unused import), F541 (empty f-string). Deleting these is safe and behavior-preserving.
- **Never** make an edit `ruff` would newly flag: don't delete the last use of an import or a referenced name (creates `F821`, which is **blocking**); don't drop a variable still in use.
- Respect isort (`I`): after removing an import, leave imports sorted/combined/sectioned — don't hand-reorder.
- Don't introduce an empty f-string (F541) when collapsing a string.
- Hard floor: keep every edit `py_compile`-clean and clean under `ruff check --select E9,F --ignore E402,F401,F541,F811,F841`.

## Scope discipline

- Edit only changed hunks unless a nearby fix is required for coherence.
- Avoid non-requested refactors and unrelated formatting churn.
- Subtractive only: verbs are **delete** and **collapse-to-original-intent**. Renaming, splitting, or restructuring → out of scope, hand to clean-code.
- If uncertain whether a pattern is intentional, keep it — or ask before changing anything.
