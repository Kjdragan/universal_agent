# UA OpenClaw Parity Live Smoke Verification

Date: 2026-02-20  
Status: Completed (pass after operational ownership remediation)

## Objective

Run a live production smoke cycle on VPS to verify:

1. Session creation bootstraps required key files.
2. Reset/delete lifecycle captures session rollover memory in shared memory.
3. Canonical memory tools (`memory_search`, `memory_get`) return hits for captured content.

## Environment

1. API: `https://api.clearspringcg.com`
2. VPS app root: `/opt/universal_agent`
3. Shared memory root: `/opt/universal_agent/Memory_System/ua_shared_workspace`
4. Branch/commit deployed: `dev-telegram` @ `85b2ccb`

## Smoke Run Evidence

## Run 1 (detected blocker)

1. Session created successfully.
2. Bootstrap files present.
3. Reset lifecycle capture returned:
   - `{"captured":false,"reason":"capture_error","error":"[Errno 13] Permission denied: '/opt/universal_agent/Memory_System/ua_shared_workspace/memory/sessions/...'}`
4. Root cause:
   - Shared memory tree ownership was `ubuntu:ubuntu` while services run as `ua`.

## Operational remediation applied

1. Ran on VPS:
   - `chown -R ua:ua /opt/universal_agent/Memory_System /opt/universal_agent/AGENT_RUN_WORKSPACES`

## Run 2 (pass)

1. Session:
   - `session_20260220_140640_39e91a8e`
2. Bootstrap verification:
   - `bootstrap_ok=True`
3. Reset capture verification:
   - `reset_status=reset`
   - `reset_memory_capture={"captured":true,"path":"memory/sessions/session_20260220_140640_39e91a8e_2026-02-20_transcript-smoke.md","source":"transcript","trigger":"ops_reset"}`
4. Delete verification:
   - `{"status":"deleted","session_id":"session_20260220_140640_39e91a8e"}`
5. Shared-memory grep hit:
   - `/opt/universal_agent/Memory_System/ua_shared_workspace/memory/sessions/session_20260220_140640_39e91a8e_2026-02-20_transcript-smoke_2.md:14:SMOKE_PARITY_MARKER_1771596401`
6. Canonical tool checks:
   - `memory_search_hit=True`
   - `memory_get_hit=True`

## Conclusion

After correcting VPS directory ownership, the live smoke cycle confirms:

1. Key-file bootstrap is functioning.
2. Session lifecycle memory capture is functioning.
3. Canonical memory tools retrieve captured memory successfully.

## Follow-up Recommendation

Codify the ownership invariant in deployment/runtime automation so this does not regress (for example: explicit `chown` on `Memory_System` and workspace roots during deploy/provisioning).
