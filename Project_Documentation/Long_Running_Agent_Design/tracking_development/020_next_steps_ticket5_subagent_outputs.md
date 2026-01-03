# Ticket 5 — Subagent Durability Outputs + RELAUNCH Skip (Next Steps)

Date: 2026-01-02

## Summary
Persisted subagent outputs as artifacts keyed by deterministic task_key and skip RELAUNCH on resume when outputs already exist. Added a unit test for output persistence.

## Why
Task outputs are not resumable via the provider. Persisting outputs prevents rework and allows resume to avoid relaunching completed subagents.

## Changes
- Added subagent output persistence: `subagent_output.json` and `subagent_summary.md` stored under `subagent_outputs/<task_key>/`.
- On resume, RELAUNCH tasks skip relaunch if output exists; ledger marks them as succeeded with a reuse receipt.
- Added a unit test for output persistence and availability checks.

## Files
- `src/universal_agent/main.py`
- `tests/test_subagent_output_persistence.py`

## Repro Command
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
```
Kill after Task completes, then resume with:
```
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

## Pass/Fail Signal
- **Pass**: Resume skips relaunch when `subagent_output.json` exists; no redundant subagent run.
- **Fail**: Subagent relaunches even when output artifacts are present.

## Regression Check
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/quick_resume_job.json
```
Kill during sleep, then resume; expect clean completion.

## Tests Run
```
UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv_cache uv run pytest tests/test_subagent_output_persistence.py
```

## Notes
- Output validation is minimal (file exists + non-empty output payload).
- Ledger entries for reused outputs are marked succeeded with metadata pointing to the output file.
