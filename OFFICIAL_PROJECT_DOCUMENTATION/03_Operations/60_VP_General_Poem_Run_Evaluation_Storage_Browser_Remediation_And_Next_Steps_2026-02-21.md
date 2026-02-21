# 60_VP_General_Poem_Run_Evaluation_Storage_Browser_Remediation_And_Next_Steps_2026-02-21

## Summary
This report evaluates the replayed "General VP poem + email" run, verifies whether the run followed the intended VP happy path, answers dashboard/file-browser questions, and records remediation implemented in this cycle.

Date: 2026-02-21

## Scope and Evidence Reviewed
1. Run workspace:
- `AGENT_RUN_WORKSPACES/session_20260220_220236_44ad66a8`
2. VP roots:
- `AGENT_RUN_WORKSPACES/vp_general_primary_external`
- `AGENT_RUN_WORKSPACES/vp_coder_primary_external`
3. Durable ledgers:
- `AGENT_RUN_WORKSPACES/vp_state.db`
- `AGENT_RUN_WORKSPACES/coder_vp_state.db`
4. UI/API implementation paths:
- Storage APIs in `src/universal_agent/api/server.py`
- Storage UI in `web-ui/components/storage/*`
- Mission guardrails in `src/universal_agent/mission_guardrails.py`

## Run Evaluation (Happy Path Verdict)
1. This specific run did **not** use the VP tool-first mission path.
2. The run used `Task` subagent delegation (`subagent_type: general-purpose`) rather than `vp_dispatch_mission`/`vp_wait_mission`.
3. Evidence in run artifacts:
- `trace.json` and `transcript.md` show `Tool Call: Task` and no `vp_*` tool usage.
4. `vp_state.db` had no mission/event/session rows for this run; therefore no General VP mission ledger entry was created.
5. The run still produced a valid poem and successfully sent Gmail, but this happened through subagent + Composio tool flow, not external VP mission flow.

## Direct Answers to User Questions
1. "Do we now have an appropriate happy path?"
- For this observed run: no. It was functionally successful but architecturally off VP happy path.

2. "Do we need to tighten repeatable approach for these queries?"
- Yes. Natural-language "use General VP" is currently still satisfiable via `Task` delegation. A stricter routing/control policy is needed if VP-tool-first is mandatory for this class of request.

3. "Are VP processes working properly as separate processes?"
- The VP infrastructure is working, but it was not invoked by this run path.
- Existing VP session identities (`vp_general_primary_external`, `vp_coder_primary_external`) are present as separate lanes.

4. "Are we getting required communication to/from outside process and Simone?"
- For runs that use VP tool flow: yes, via mission/event bridge and durable VP ledger.
- For this run: no VP mission communication occurred because no VP mission was dispatched.

5. "Should the General VP appear in session groups?"
- Yes. VP lanes appear as their own session-like rows (`vp_general_primary_external`, `vp_coder_primary_external`).
- A mission appears in "Recent VP missions" only when a VP mission is actually dispatched to VP DB.

6. "Why is Recent VP Missions showing stale failed coder mission instead of this poem run?"
- Because this poem run never created a VP mission row; the dashboard correctly showed the last real VP mission from ledger history.

## Additional Issues Found
1. Mission completion status false-negative:
- `goal_satisfaction` marked run as failed for missing email send even though Gmail send succeeded.
- Root cause: guardrail counter only tracked top-level tool names; Gmail send occurred inside `COMPOSIO_MULTI_EXECUTE` nested payload.

2. Storage/File Browser root mismatch:
- Storage UI was reading VPS mirror roots by default in this context, not canonical local workspace roots.
- Mirror root only contained `downloads/memory/work_products`, so expected session directories were not visible.

3. Storage sessions listing noise:
- Non-session directories were being surfaced in sessions table when reading mirror roots.

## Remediation Implemented in This Cycle
1. Storage root selection (local vs mirror) added end-to-end:
- API supports `root_source=local|mirror` for sessions/artifacts/overview/files/file.
- UI adds root-source controls and uses selected source for all storage tabs.

2. File Browser visibility issue fixed:
- Local root browsing now available and defaulted for localhost usage patterns.
- Sessions list now filters out non-session folders and properly classifies VP folders.

3. Explorer bulk cleanup capability implemented:
- Added multi-select + batch delete in explorer UI.
- Added safe backend delete endpoint: `POST /api/vps/files/delete`.

4. Mission guardrail nested Gmail detection fixed:
- `COMPOSIO_MULTI_EXECUTE` nested tool slugs are now parsed for email-send accounting.

## Validation Performed
1. Unit tests:
- `tests/unit/test_mission_guardrails.py`
- `tests/unit/test_storage_api_helpers.py`
- Result: passing.

2. Frontend quality gates:
- `web-ui` lint passed.
- `web-ui` production build passed.

## Current Status After Remediation
1. You can now inspect the requested session and VP directories through Storage using `root_source=local`.
2. You can select and delete files/folders in Storage Explorer for cleanup workflows.
3. Mission guardrails now correctly recognize nested Gmail-send calls in multi-execute payloads.
4. The remaining gap for strict VP happy path is routing policy enforcement from user intent to `vp_*` tools.

## Recommended Next Tightening Step (Policy)
1. Add a control-plane guardrail that blocks `Task`-based delegation when user intent explicitly targets external VP execution, and requires `vp_dispatch_mission` + `vp_wait_mission`.
2. Keep `Task` available for general specialist delegation, but enforce VP-specific lane selection on explicit VP intents.
