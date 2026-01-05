# 029: Durability Evaluation Report (Post Fix #3)

**Date:** 2026-01-05  
**Scope:** Full runbook execution after malformed replay guardrail fix + idempotency normalization.

## Changes Under Test
- Malformed tool name guardrail now blocks replay-time malformed names without setting `waiting_for_human`.
- COMPOSIO multi-execute idempotency normalization (session metadata ignored).

## Test Matrix Results (Runbook)

**Test 1: Task crash (RELAUNCH)**
- **Run ID:** `5c31beeb-2b55-499b-b262-57c2ff97f438`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_031108`
- **Main Trace ID:** `019b8d6dd0fb63c8b11d53032b31df4f`
- **Status:** succeeded
- **Notes:** Relaunch completed; email sent once (ID: `19b8d6fd92572487`).

**Test 2: PDF render crash (Bash)**
- **Run ID:** `c9c992bd-b606-43fa-adff-eb3ef1ac6587`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_031452`
- **Main Trace ID:** `019b8d7136496674160ade531cc9eb31`
- **Status:** succeeded
- **Notes:** Bash replay clean; email sent once (ID: `19b8d72f9b3efa50`).

**Test 3: Upload crash (upload_to_composio)**
- **Run ID:** `ff104266-7a17-40a9-ba84-230c7013adc0`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_032136`
- **Main Trace ID:** `019b8d7af8c1367b28241b5f0ec4338d`
- **Status:** succeeded
- **Notes:** Required an extra resume attempt to drain replay queue; email sent once (ID: `19b8d7a7e66ef66f`).

**Test 4: Post-email / pre-ledger crash (COMPOSIO_MULTI_EXECUTE_TOOL)**
- **Run ID:** `2ff7645e-1843-4c78-b4f4-91904a1e913a`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_033046`
- **Main Trace ID:** `019b8d81d950abea08b93b906deb46b9`
- **Status:** succeeded
- **Notes:** Resume completed; evidence summary still flagged missing receipts even though tool calls succeeded.

**Test 5: Read-only replay**
- **Run ID:** `a00d599d-af95-4b7f-a3b4-d16253a3429e`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_033556`
- **Main Trace ID:** `019b8d83e29428354de3cb3dabe88d38`
- **Status:** succeeded
- **Notes:** list_directory replayed; read_local_file completed.

**Test 6: Replay drain continuation**
- **Run ID:** `0bef8d2a-f9fc-4c8e-999e-1719cfd9fe5e`
- **Workspace:** `AGENT_RUN_WORKSPACES/session_20260105_033742`
- **Main Trace ID:** `019b8d899e7e76a2867be04caaaaa37d`
- **Status:** succeeded
- **Notes:** Required an extra resume to finalize job completion; email sent once (ID: `19b8d89021bab95b`).

## Issues / Findings
1. **Resume timeouts caused partial logs for Tests 3 and 6.**
   - Additional resume attempts completed both runs.
2. **Forced replay still blocks some Bash env checks.**
   - `echo $UA_TEST_EMAIL_TO` was denied during Test 6 forced replay.
3. **Evidence summary mismatch in Test 4.**
   - Summary flagged missing receipts even though uploads/emails occurred; likely due to crash stage and pending receipt handling.

## Evidence Artifacts
- Logs:
  - `/tmp/runbook_full_test1b_crash_fix3.log`
  - `/tmp/runbook_full_test1b_resume_fix3.log`
  - `/tmp/runbook_full_test2_crash_fix3.log`
  - `/tmp/runbook_full_test2_resume_fix3.log`
  - `/tmp/runbook_full_test3b_crash_fix3.log`
  - `/tmp/runbook_full_test3b_resume_fix3.log`
  - `/tmp/runbook_full_test4b_crash_fix3.log`
  - `/tmp/runbook_full_test4b_resume_fix3.log`
  - `/tmp/runbook_full_test5_crash_fix3.log`
  - `/tmp/runbook_full_test5_resume_fix3.log`
  - `/tmp/runbook_full_test6_crash_fix3.log`
  - `/tmp/runbook_full_test6_resume_fix3.log`
- Workspaces:
  - `AGENT_RUN_WORKSPACES/session_20260105_031108`
  - `AGENT_RUN_WORKSPACES/session_20260105_031452`
  - `AGENT_RUN_WORKSPACES/session_20260105_032136`
  - `AGENT_RUN_WORKSPACES/session_20260105_033046`
  - `AGENT_RUN_WORKSPACES/session_20260105_033556`
  - `AGENT_RUN_WORKSPACES/session_20260105_033742`

## Recommendations
- **Keep malformed replay guardrail fix** (no regressions observed).
- **Whitelist Bash env checks during forced replay** to reduce noise.
- **Investigate evidence-summary receipts for crash stage in Test 4** to ensure receipt promotion logic is reflected in summary output.
