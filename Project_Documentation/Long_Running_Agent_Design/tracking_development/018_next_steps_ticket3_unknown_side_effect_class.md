# Ticket 3 — Unknown side_effect_class Fail-Safe (Next Steps)

Date: 2026-01-02

## Summary
Added a guardrail that treats unknown/missing side_effect_class as external, logs a warning once per run, and ensures dedupe protection remains conservative. Added a unit test to verify dedupe is enforced when the class is invalid.

## Why
If side_effect_class is missing or invalid, the system must default to conservative behavior to prevent duplicate side effects after resume.

## Changes
- Added canonical side-effect class set and normalization with a once-per-run warning.
- Updated idempotency dedupe logic to use normalized side_effect_class.
- Added unit test for invalid side_effect_class behavior.

## Files
- `src/universal_agent/main.py`
- `tests/test_side_effect_class_guardrail.py`

## Repro Command
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json
```
Kill during sleep, then resume:
```
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

## Pass/Fail Signal
- **Pass**: Unknown/invalid side_effect_class still dedupes and skips tool execution.
- **Fail**: Invalid side_effect_class causes a repeated external tool call.

## Regression Check
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/quick_resume_job.json
```
Kill during sleep, then resume; expect clean completion.

## Tests Run
```
UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv_cache uv run pytest tests/test_side_effect_class_guardrail.py
```

## Notes
- Warning is logged once per run/tool/class combination.
