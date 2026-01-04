# Durability Testing Master Test

This document defines the full durability test suite used to validate crash/replay behavior, idempotency, and replay correctness across side-effecting tools and sub-agent relaunch.

## Goals
- Prove idempotency: no duplicate external side effects after resume.
- Prove replay correctness: in-flight tool calls re-run deterministically.
- Prove Task relaunch: sub-agent work is reissued or reused from artifacts.
- Prove crash-hook reliability: raw vs tool_slug matching is deterministic.

## Prerequisites
- Repo root: `/home/kjdragan/lrepos/universal_agent`
- Env:
  - `PYTHONPATH=src`
  - `UA_TEST_EMAIL_TO=<email>` for email steps
- Jobs:
  - `tmp/relaunch_resume_job.json`
  - `tmp/read_only_resume_job.json`
- Runtime DB:
  - `AGENT_RUN_WORKSPACES/runtime_state.db`

## Crash Hook Controls
- `UA_TEST_CRASH_AFTER_TOOL=<tool>`
- `UA_TEST_CRASH_AFTER_TOOL_CALL_ID=<id>` (optional, precise)
- `UA_TEST_CRASH_STAGE=after_tool_success_before_receipt|after_tool_success_before_ledger_commit|after_ledger_mark_succeeded`
- `UA_TEST_CRASH_MATCH=raw|slug|any` (default: `raw`)

## Master Matrix (Canonical Tool Names)
1) **Task crash (RELAUNCH)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Task`
   - Resume: relaunch Task with same inputs or reuse `subagent_output.json`.
   - Expect: no TaskOutput; one sub-agent output.

2) **PDF render crash (Bash)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=Bash`
   - Resume: re-run PDF render, then upload + email once.
   - Expect: single PDF, single upload, single email.

3) **Upload crash (mcp__local_toolkit__upload_to_composio)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=upload_to_composio`
   - Resume: upload replayed once, email once.
   - Expect: no duplicate email; upload receipt reused.

4) **Post-email / pre-ledger crash (mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL`
     + `UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit`
   - Resume: pending receipt promoted; no re-send.
   - Expect: no duplicate email.

5) **Read-only replay (mcp__local_toolkit__list_directory + read_local_file)**
   - Crash: `UA_TEST_CRASH_AFTER_TOOL=mcp__local_toolkit__list_directory`
   - Resume: list_directory replayed, read_local_file succeeds.
   - Expect: no side effects; replay is safe.

## Artifacts
Each run creates:
- `AGENT_RUN_WORKSPACES/session_<timestamp>/run.log`
- `AGENT_RUN_WORKSPACES/session_<timestamp>/transcript.md`
- `AGENT_RUN_WORKSPACES/session_<timestamp>/trace.json`
- `Project_Documentation/Long_Running_Agent_Design/KevinRestartWithThis.md`

## Invariants
- No duplicate side effects (email/upload).
- Task relaunch does not call TaskOutput/TaskResult.
- Replay queue contains only the expected tool calls.

## Related Sub-agent Evaluation (Latest)
- **Letta sub-agent capture + history**: `uv run python tests/test_letta_subagent.py`
  - ✅ Sub-agent messages captured and visible in Letta.
- **Sub-agent isolation**: `uv run python tests/test_letta_subagent_isolation.py`
  - ✅ Sub-agent memory/messages do not leak into primary agent.
- **Report run confirmation** (manual): trigger a report Task and verify Letta lists
  `universal_agent report-creation-expert` with its own memory blocks/messages.
