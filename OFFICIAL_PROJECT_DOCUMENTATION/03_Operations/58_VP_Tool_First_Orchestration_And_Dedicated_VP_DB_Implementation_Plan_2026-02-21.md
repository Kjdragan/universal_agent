# 58_VP_Tool_First_Orchestration_And_Dedicated_VP_DB_Implementation_Plan_2026-02-21

## Summary
Implement a generalized, reusable approach for Simone to operate external primary agents (CODIE and Generalist) without prompt bloat or ad-hoc shell/API probing.

The approach is:
1. Separate VP persistence from core runtime persistence.
2. Expose first-class internal VP tools for mission lifecycle.
3. Keep system prompt thin and move operational procedure into a VP orchestration skill.
4. Ensure bidirectional mission communication (dispatch, progress, completion, failure) is visible in Simone session flows and dashboard.
5. Fix current reliability blockers before behavior changes.

## Confirmed Root Causes
1. Simone has no first-class internal `vp_*` tool contract, so it improvised with code search and `curl`.
2. VP dispatch currently contends on runtime DB writes, producing `sqlite3.OperationalError: database is locked`.
3. VP missions endpoint returns 500 on mixed datetime types (`offset-naive` vs `offset-aware`) when computing mission duration.
4. External VP workspace roots remained empty in the failing scenario; work output fell back into Simone session workspace.

## Public APIs / Interfaces / Type Changes

### External HTTP API (Gateway)
1. Keep existing VP endpoints, but rewire storage to `vp_state.db`:
- `GET /api/v1/ops/vp/sessions`
- `GET /api/v1/ops/vp/missions`
- `POST /api/v1/ops/vp/missions/dispatch`
- `POST /api/v1/ops/vp/missions/{mission_id}/cancel`
- `GET /api/v1/ops/metrics/vp`
2. Improve error contract:
- Validation failures: `400`.
- Lock/transient backend contention: `503` with retryable detail.
- Unexpected failures: `500` with request id/log correlation.

### Internal MCP Tool Contract (New, Tool-First)
1. `vp_dispatch_mission`
- Inputs: `vp_id`, `objective`, `mission_type`, `constraints`, `budget`, `idempotency_key`, `priority`, `reply_mode`.
- Output: `mission_id`, `status`, `vp_id`, `queued_at`.
2. `vp_get_mission`
- Inputs: `mission_id`.
- Output: mission state, result ref, timestamps, failure detail.
3. `vp_list_missions`
- Inputs: `vp_id`, `status`, `limit`.
- Output: mission list.
4. `vp_wait_mission`
- Inputs: `mission_id`, `timeout_seconds`, `poll_seconds`.
- Output: terminal mission state or timeout.
5. `vp_cancel_mission`
- Inputs: `mission_id`, `reason`.
- Output: cancel request accepted/not accepted.
6. `vp_read_result_artifacts`
- Inputs: `mission_id`, `max_files`, `max_bytes`.
- Output: summarized artifact index + top file excerpts.

### Skill + Prompt Contract
1. New skill: `vp-orchestration`.
2. Prompt change is minimal:
- “For external primary-agent execution, use `vp_*` tools only.”
- “Do not call gateway VP HTTP endpoints via shell/curl.”

## Implementation Plan

### Phase A — Reliability and Storage Isolation (P0)
1. Add dedicated VP DB path:
- `vp_state.db` via `UA_VP_DB_PATH` override, default in `AGENT_RUN_WORKSPACES`.
2. Route all VP queue/session/event operations to VP DB:
- Gateway VP endpoints read/write VP DB connection.
- VP workers read/write VP DB.
- Keep `runtime_state.db` for core session runtime only.
3. Preserve coder lane DB isolation already used for legacy/in-process CODIE state.
4. Fix datetime parsing in mission serialization:
- Normalize parsed datetimes to consistent timezone handling before subtraction.
5. Add explicit retry wrapper for VP dispatch writes:
- bounded retries with short backoff on lock contention.
6. Add structured gateway logging for VP dispatch/list errors.

### Phase B — First-Class VP Internal Tools (P0)
1. Create new VP tool module under `src/universal_agent/tools/`.
2. Register wrappers in `internal_registry`.
3. Ensure these tools call gateway-level VP operations through in-process function path, not shell commands.
4. Ensure deterministic tool responses with machine-parseable mission fields.
5. Add read-only artifact summarization tool output for quick “what did VP produce?” checks.

### Phase C — Behavioral Guidance without Prompt Bloat (P1)
1. Add `vp-orchestration` skill with:
- dispatch criteria
- wait/poll rules
- failure fallback rules
- artifact handoff rules
- when to use CODIE vs Generalist
2. Add thin system prompt rule pointing to `vp_*` tools and skill usage.
3. Keep heavy procedure out of global prompt text.

### Phase D — Bidirectional Communication and UX (P1)
1. Ensure mission payload includes source session metadata.
2. Stream mission lifecycle events back into originating Simone session channel:
- `vp.mission.dispatched`
- `vp.mission.started`
- `vp.mission.completed`
- `vp.mission.failed`
- `vp.mission.cancelled`
3. Ensure dashboard VP panel and chat activity views show same mission truth and links to artifacts/result refs.
4. Normalize stale session handling so inactive workers do not appear as running.

### Phase E — CODIE and Generalist Parity (P1)
1. Enforce identical mission-control interface for both agents via `vp_id`.
2. Maintain CODIE guardrails blocking UA-repo-internal modifications unless explicitly allowlisted.
3. Ensure Generalist has equivalent lifecycle observability and artifact conventions.

### Phase F — Testing and Verification (P0/P1)
1. Unit tests:
- VP DB path resolution and connection use.
- Datetime duration calculations with mixed timezone inputs.
- VP tool schema validation and required fields.
2. Integration tests:
- Dispatch to `vp.general.primary`, execute a simple file-producing mission, verify terminal state and artifacts.
- Dispatch to `vp.coder.primary` with policy-compliant target path, verify lifecycle and artifact reporting.
- Verify mission events appear in originating session feed.
3. Concurrency tests:
- Simultaneous dispatch/list/cancel with worker polling against `vp_state.db`.
- Assert no lock-induced hard failure under normal load.
4. Regression tests:
- Core non-VP session operations unaffected.
- Existing coder-vp compatibility metrics still resolve correctly.

## Exact Code Touchpoints
1. `src/universal_agent/durable/db.py`
2. `src/universal_agent/gateway.py`
3. `src/universal_agent/gateway_server.py`
4. `src/universal_agent/vp/dispatcher.py`
5. `src/universal_agent/vp/worker_main.py`
6. `src/universal_agent/vp/worker_loop.py`
7. `src/universal_agent/tools/internal_registry.py`
8. `src/universal_agent/tools/` (new VP tools module)
9. `src/universal_agent/prompt_builder.py`
10. `.agents/skills/` (new `vp-orchestration` skill)
11. `tests/gateway/`
12. `tests/durable/`
13. `tests/integration/`
14. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/58_...`

## Test Cases and Scenarios
1. Simone asks Generalist VP for a poem:
- `vp_dispatch_mission` is used.
- Mission reaches terminal state.
- Artifact exists under VP external workspace mission folder.
- Result is visible in chat activity and VP dashboard.
2. Simone asks CODIE for greenfield coding task:
- Mission is dispatched to CODIE with same tool flow.
- Guardrail blocks forbidden UA-internal path targets.
3. Simultaneous VP activity:
- CODIE + Generalist active concurrently.
- No runtime DB contention with core session actions.
- VP queue remains consistent.
4. API error behavior:
- lock/transient failures return retryable status/details.
- no generic opaque 500 for expected contention conditions.
5. Mission listing:
- no datetime subtraction 500 in `/api/v1/ops/vp/missions`.

## Rollout Plan
1. Deploy Phase A first behind existing VP dispatch flags.
2. Run targeted VP API and worker smoke checks.
3. Deploy Phase B tools and enable tool-first workflow.
4. Deploy Phase C skill + thin prompt rule.
5. Deploy Phase D event bridge + UX visibility refinements.
6. Run full regression and 24h observation for lock/fallback/error-rate metrics.

## Assumptions and Defaults
1. One shared `vp_state.db` for all external VPs is the default.
2. Per-VP DB sharding is deferred until throughput/isolation demands it.
3. External workers remain autonomous executors; Simone remains control-plane orchestrator.
4. Tool-first orchestration is mandatory; shell/curl VP control is non-standard and discouraged.
5. CODIE and Generalist remain equivalent at mission-control API/tool layer via `vp_id`.
