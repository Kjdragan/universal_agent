# Forced Replay Task Hardening

Date: 2026-01-03

## Summary
Improved forced-replay stability for RELAUNCH Tasks by normalizing Task prompt whitespace during replay matching, allowing subagent tool calls while a forced Task is active, and providing resume guidance to use absolute workspace paths. Added tests to cover replay matching and subagent tool allowance during forced replay.

## Why
Task tools are ephemeral and must be relaunched after resume. Forced replay was brittle when the model reformatted Task prompts or when subagent tools were blocked by the replay gate. These changes reduce false-denies while preserving deterministic replay flow.

## Changes
- Normalize Task prompt whitespace when matching forced replay inputs.
- Permit subagent tool calls while a forced Task replay is active.
- Resume prompt now instructs absolute workspace paths to avoid relative-path failures.

## Files
- `src/universal_agent/main.py`
- `tests/test_forced_tool_matches.py`
- `tests/test_forced_replay_task_children.py`

## Tests Run
```
PYTHONPATH=src uv run pytest tests/test_forced_tool_matches.py
PYTHONPATH=src uv run pytest tests/test_forced_replay_task_children.py
```

## Notes
- Forced replay still requires a matching tool name/namespace; prompt differences beyond whitespace still block and will set the run to waiting_for_human if replay cannot complete.
- Validation: resumed Task replay completed cleanly (run_id `8aea1a73-885a-4009-b5c6-e3245af4691c`) and subagent tool calls executed without forced-replay denials; email sent (message id `19b829b7b3bd42f1`).
