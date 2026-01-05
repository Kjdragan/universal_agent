# 028: Durability Evaluation Report (Post Fix #2)

**Date:** 2026-01-05  
**Updated:** 2026-01-05 (Test 2 rerun + malformed replay guardrail fix)
**Scope:** Full runbook execution after COMPOSIO_MULTI_EXECUTE_TOOL idempotency fix + new unit test.

## Changes Under Test
- Idempotency normalization updated for `COMPOSIO_MULTI_EXECUTE_TOOL` to ignore session metadata in idempotency hashing (`session_id`, `current_step`, `current_step_metric`, `sync_response_to_workbench`, `thought`).
- Unit test added to lock this behavior.
- Malformed tool name guardrail improved to block replay-time malformed tool calls without setting run status to `waiting_for_human`.
- Added regression test to ensure forced replay does not stall on malformed tool names.

## Test Matrix Results (Runbook)

**Test 1: Task crash (RELAUNCH)**
- **Run ID:** `a894b9a0-2648-4c2b-a419-3a28ff9a2d40`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_021627`
- **Main Trace ID:** `019b8d3c0a73b7c2dbf552893f5bd84d`
- **Status:** succeeded
- **Notes:** Recovery replay initially blocked Bash in forced replay, then relaunched Task and completed. Email sent once.

**Test 2: PDF render crash (Bash)**
- **Run ID:** `1c3201c3-4802-4dff-8c47-5cb7ae601f02`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_030124`
- **Main Trace ID:** `019b8d64e75ee342ff7799f1742095ed`
- **Status:** succeeded
- **Notes:** Replay resumed cleanly after Bash crash; email sent once.  
  **Prior run (pre-fix):** `c26368c7-27d0-49d1-84f0-769538809644` ended `waiting_for_human` due to a malformed tool name during resume.

**Test 3: Upload crash (upload_to_composio)**
- **Run ID:** `1790896d-a63e-4b02-b449-7cbe27c5b8ae`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_022340`
- **Main Trace ID:** `019b8d435dab8442412c79cde4dad030`
- **Status:** succeeded
- **Notes:** Resume replayed upload once and sent email once. No duplicate send observed.

**Test 4: Post-email / pre-ledger crash (COMPOSIO_MULTI_EXECUTE_TOOL)**
- **Run ID:** `39c74273-ef0e-459d-b172-c558a8a9ae0e`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_022702`
- **Main Trace ID:** `019b8d46981c5e6abfe06e248a93fb88`
- **Status:** succeeded
- **Notes:** Pending receipt promoted; resume completed without re-sending.

**Test 5: Read-only replay**
- **Run ID:** `ade38647-fbcf-499a-a9a6-21f72ffda26f`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_023009`
- **Main Trace ID:** `019b8d48482237c7aa215cb3842eea4b`
- **Status:** succeeded
- **Notes:** list_directory replayed and read_local_file succeeded.

**Test 6: Replay drain continuation**
- **Run ID:** `f5f0875f-db1d-4bd5-bdf4-15eeb9b6457c`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_023237`
- **Main Trace ID:** `019b8d4fa8f4d188e4e48b11b957fa53`
- **Status:** succeeded
- **Notes:** Resume confirmed completed side effects and marked job complete. No duplicate email send observed.

## Unit Test Coverage
- `tests/test_durable_ledger.py` now includes `test_multi_execute_idempotency_ignores_session_metadata`.
- Test run: `PYTHONPATH=src uv run pytest tests/test_durable_ledger.py` (8/8 passed).
- `tests/test_forced_replay_malformed_tool_name.py` added for malformed replay guardrail behavior.
- Test run: `PYTHONPATH=src uv run pytest tests/test_forced_replay_malformed_tool_name.py` (1/1 passed).

## Issues / Findings
1. **Resolved: malformed tool call guardrail now blocks replay-time malformed names without stalling.**
   - Test 2 rerun succeeded after the guardrail fix.
2. **Forced replay guard can block unrelated Bash calls (Test 1).**
   - PreToolUse denied `sleep` and `ls` during forced replay, then resumed successfully. This is recoverable but noisy.
3. **Crash hook still produces "Tool permission stream closed" errors.**
   - Expected during forced exits; keep as known behavior.

## Evidence Artifacts
- Logs: `/tmp/runbook_full_test{1..6}_{crash,resume}.log`
- Test 2 rerun logs: `/tmp/runbook_test2_crash_fix2.log`, `/tmp/runbook_test2_resume_fix2.log`
- Workspaces:
  - `AGENT_RUN_WORKSPACES/session_20260105_021627`
  - `AGENT_RUN_WORKSPACES/session_20260105_022037` (pre-fix Test 2)
  - `AGENT_RUN_WORKSPACES/session_20260105_022340`
  - `AGENT_RUN_WORKSPACES/session_20260105_022702`
  - `AGENT_RUN_WORKSPACES/session_20260105_023009`
  - `AGENT_RUN_WORKSPACES/session_20260105_023237`
  - `AGENT_RUN_WORKSPACES/session_20260105_030124` (Test 2 rerun)

## Recommendations
- **Guardrail fix completed:** malformed tool names are denied without forcing `waiting_for_human` in forced replay; keep this behavior.
- **Consider softening forced replay Bash deny:** allow `sleep` and read-only checks during replay (or whitelist) to reduce false blockers.
- **Continue monitoring COMPOSIO_MULTI_EXECUTE_TOOL dedupe:** current fix worked in Test 3 and Test 6.
