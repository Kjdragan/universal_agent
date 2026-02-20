# 53 UA External Primary Agent Runtime Implementation Plan (2026-02-20)

## Goal
Establish an external-primary-agent execution model where Simone remains control plane and VP agents run as separate worker services driven by DB mission contracts.

## Implemented Scope
1. Generic VP profile system with two enabled primary profiles:
   - `vp.coder.primary`
   - `vp.general.primary`
2. DB mission envelope dispatch and lifecycle operations:
   - queue
   - claim
   - claim heartbeat
   - cancel request
   - finalize
3. External worker runtime loop:
   - poll + claim
   - mission execution via client runtime
   - mission events and status updates
4. CODIE hard-path guardrails:
   - dispatch-time and worker-time block against UA repo/runtime roots
   - allowlisted handoff root support
5. New VP ops APIs:
   - `GET /api/v1/ops/metrics/vp`
   - `GET /api/v1/ops/vp/sessions`
   - `GET /api/v1/ops/vp/missions`
   - `POST /api/v1/ops/vp/missions/dispatch`
   - `POST /api/v1/ops/vp/missions/{mission_id}/cancel`
6. Compatibility preserved:
   - `GET /api/v1/ops/metrics/coder-vp`
   - `GET /api/v1/dashboard/metrics/coder-vp`

## Runtime Model
1. Dispatch path
   - Gateway routes eligible CODIE requests to external mission queue when:
     - `UA_VP_EXTERNAL_DISPATCH_ENABLED=1`
     - `UA_VP_DISPATCH_MODE=db_pull`
   - Otherwise legacy in-process CODIE lane remains active.
2. Worker path
   - Worker instances run independently per `vp_id`.
   - Each worker claims queued missions, executes, emits mission events, and finalizes state.

## Operational Commands
1. Start CODIE worker:
```bash
scripts/start_vp_worker.sh vp.coder.primary
```
2. Start GENERALIST worker:
```bash
scripts/start_vp_worker.sh vp.general.primary
```
3. Dispatch test mission:
```bash
curl -X POST http://localhost:8002/api/v1/ops/vp/missions/dispatch \
  -H "Content-Type: application/json" \
  -d '{"vp_id":"vp.general.primary","mission_type":"task","objective":"Create a concise execution checklist.","constraints":{},"budget":{}}'
```

## Environment Additions
Use `.env.sample` entries:
- `UA_VP_DISPATCH_MODE`
- `UA_VP_ENABLED_IDS`
- `UA_VP_EXTERNAL_DISPATCH_ENABLED`
- `UA_VP_HARD_BLOCK_UA_REPO`
- `UA_VP_CODER_WORKSPACE_ROOT`
- `UA_VP_GENERAL_WORKSPACE_ROOT`
- `UA_VP_POLL_INTERVAL_SECONDS`
- `UA_VP_LEASE_TTL_SECONDS`
- `UA_VP_MAX_CONCURRENT_MISSIONS`
- `UA_VP_HANDOFF_ROOT`

## Notes
1. External dispatch is intentionally default-off until worker services are running.
2. CODIE remains reserved for significant external greenfield work.
3. Simone remains primary user-facing orchestrator.
