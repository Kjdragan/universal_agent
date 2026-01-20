# Durability Troubleshooting (Code-Verified)

## Symptom: Resume stuck in replay
**Likely cause:** model did not reissue the in-flight tool call.
**Fix:** deterministic Task relaunch or output reuse should drain the queue.

Code: `src/universal_agent/main.py` → `reconcile_inflight_tools`.

## Symptom: Duplicate side effects
**Likely cause:** missing receipt or crash before ledger commit.
**Check:** `tool_calls` table for duplicate idempotency keys.

## Symptom: Missing email recipient resolution ("me")
**Cause:** identity registry not loaded or `UA_PRIMARY_EMAIL` unset.
**Fix:** set `UA_PRIMARY_EMAIL` and optional `UA_EMAIL_ALIASES`.

Code: `src/universal_agent/identity/registry.py`.

## Symptom: COMPOSIO_REMOTE_WORKBENCH invoked during durability replay
**Cause:** agent detour during recovery.
**Fix:** durable job mode blocks this tool.

Code: `src/universal_agent/main.py` pre-tool guardrails.

## Symptom: Tool permission stream closed
**Cause:** crash hook exits mid-call.
**Fix:** expected during synthetic crash tests; resume run should proceed.

