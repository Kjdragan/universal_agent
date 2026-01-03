# Ticket 8 — Durability Test Matrix (Next Steps)

Date: 2026-01-02

## Summary
Added a documented durability test matrix with kill points, commands, expected outcomes, and DB checks.

## Why
A repeatable matrix makes it easier to validate durability across the sharp edges and catch regressions after changes.

## Changes
- Added `docs/durability_test_matrix.md` with required kill points and DB verification queries.

## Files
- `docs/durability_test_matrix.md`

## Repro Command
See `docs/durability_test_matrix.md` for full commands.

## Pass/Fail Signal
- **Pass**: All matrix scenarios resume cleanly; DB queries show zero duplicate idempotency keys.
- **Fail**: Duplicate side effects, missing artifacts, or in-flight replay fails.

## Regression Check
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/quick_resume_job.json
```
Kill during sleep, then resume; expect clean completion.

## Tests Run
- Not run (documentation-only change).
