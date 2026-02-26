# 77. CSI Todoist Sync Debugging Lessons (2026-02-26)

## Objective
Capture the root cause and operational lessons from the CSI -> Todoist sync failure investigation so future incidents are faster to diagnose and safer to fix.

## Incident Summary
Observed symptom: CSI insight notifications were being created, but Todoist task creation failed with repeated dashboard errors:

1. `Todoist Sync Failed`
2. `Could not sync CSI task to Todoist...`
3. `Error: TODOIST_API_TOKEN is required`

Investigation date: 2026-02-26.

## Root Cause
Primary root cause was runtime credential availability in the failing process context, not Todoist taxonomy:

1. The CSI route constructs `TodoService()` in `signals_ingest_endpoint`.
2. `TodoService` accepts either `TODOIST_API_TOKEN` or `TODOIST_API_KEY`.
3. In the failing runtime, neither credential was effectively present when the failure occurred.

## What Was Misleading
1. Error text previously said only `TODOIST_API_TOKEN is required`, while code accepted both env vars.
2. Notification text suggested "token and taxonomy" without clearly naming the accepted credentials.
3. `/proc/<pid>/environ` can miss env vars loaded at runtime via `python-dotenv`; absence there is not definitive proof of missing values in `os.environ`.

## Code-Level Improvements Implemented
Two hardening changes were applied:

1. `src/universal_agent/services/todoist_service.py`
   1. Updated raised message to: `TODOIST_API_TOKEN or TODOIST_API_KEY is required`.
2. `src/universal_agent/gateway_server.py`
   1. CSI Todoist sync failure notification now names both accepted credentials.
   2. Added explicit debug flags for presence of both vars.
   3. Upgraded logger call to `logger.exception(...)` for stack traces.

## Reproduction and Verification Pattern
Use this sequence for deterministic debugging:

1. Confirm service mode and ingest gate:
   1. `curl http://127.0.0.1:8002/api/v1/signals/ingest` should clearly show whether ingest is disabled.
2. Validate Todoist path directly:
   1. `curl http://127.0.0.1:8002/api/v1/dashboard/todolist/actionable`
3. Run controlled end-to-end test with temporary gateway and signed CSI payload:
   1. Set `UA_SIGNALS_INGEST_ENABLED=true`
   2. Set `UA_SIGNALS_INGEST_SHARED_SECRET=<secret>`
   3. POST a correctly signed payload to `/api/v1/signals/ingest`
4. Check outcomes:
   1. CSI notification exists.
   2. Todoist task exists.
   3. No `Todoist Sync Failed` error notification appears.

## Operational Guardrails
1. Do not bypass `_verify_auth` in committed code for debugging.
2. Test with a temporary process override or dedicated local test port.
3. Prefer root-cause proof over speculative fixes:
   1. Validate token path.
   2. Validate taxonomy path.
   3. Validate signed ingest path.

## Implementation Status
Completed in current branch:

1. Documentation created.
2. Runtime error messaging improved.
3. Diagnostic clarity improved for next incident.
