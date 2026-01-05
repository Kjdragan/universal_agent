# Runtime and Replay Mechanics (Code-Verified)

Derived from:
- `src/universal_agent/main.py`
- `src/universal_agent/durable/` (ledger, checkpoints)

## Crash Hook Controls
These are implemented in the durability crash hooks:
- `UA_TEST_CRASH_AFTER_TOOL=<tool>`
- `UA_TEST_CRASH_AFTER_TOOL_CALL_ID=<id>`
- `UA_TEST_CRASH_STAGE=after_tool_success_before_receipt|after_tool_success_before_ledger_commit|after_ledger_mark_succeeded`
- `UA_TEST_CRASH_MATCH=raw|slug|any`

## Replay Policies
Replay policy is assigned per tool call:
- `REPLAY_EXACT`: re-run the exact tool call with same input.
- `RELAUNCH`: re-create Task call or reuse output if artifacts exist.

## Replay Queue
- Replay queue is built from in-flight tool calls in the runtime DB.
- Replay runs before normal continuation.
- Replay is forced: only queued tools should run.

## Deterministic Task Relaunch
Task replay is deterministic when:
- `subagent_outputs/<task_key>/subagent_output.json` exists.
- Any output file referenced in the Task prompt exists.

This avoids relying on model judgments during replay.

## Phase Checkpoints
Checkpoints used as resume anchors:
- `pre_read_only`
- `pre_side_effect`
- `post_replay`

These are persisted in `checkpoints` for the run.

