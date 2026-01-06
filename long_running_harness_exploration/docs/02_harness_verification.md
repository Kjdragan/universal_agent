# Harness Verification Protocol

To ensure the Harness System (restart logic, persistence, and handoff) works correctly, we use a dedicated verification script.

## Verification Script
`scripts/verify_harness.py`

This script simulates a long-running task by:
1. Launching the agent in a test workspace.
2. Setting `UA_DEBUG_FORCE_HANDOFF=1` (forces hooks to consider handoff logic).
3. Feeding the agent a mocked prompt/instruction set.

### Test Scenario
The script verifies a 4-phase "Ralph Wiggum" loop:
1. **Phase 1**: Agent creates `part1.txt` and stops *without* the completion promise.
   - **Expected**: Harness detects missing promise -> Restarts agent (Iteration 1).
2. **Phase 2 (Resumed)**: Agent (with wiped memory) sees instructions to wait.
   - **Expected**: (In simulation, we skip this or combine it).
3. **Phase 3**: Agent creates `part2.txt`.
4. **Phase 4**: Agent outputs "TASK_COMPLETE".
   - **Expected**: Harness detects promise -> Ends run.

### Running the Verification
```bash
PYTHONPATH=src uv run python scripts/verify_harness.py
```

### Success Criteria
1. **Database**: `iteration_count` > 0.
2. **Artifacts**: `part1.txt` and `part2.txt` exist (proving work across restarts).
3. **Completion**: Run status is `succeeded`.
4. **Output**: The string "HARNESS RESTART TRIGGERED" appears in stdout.

## Manual Verification
You can also manually verify by running the agent and using the `/harness` command (see `03_how_to_use.md`).
