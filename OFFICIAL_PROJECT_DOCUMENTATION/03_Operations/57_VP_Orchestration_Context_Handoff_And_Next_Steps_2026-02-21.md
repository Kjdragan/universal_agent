# 57_VP_Orchestration_Context_Handoff_And_Next_Steps_2026-02-21

## Summary
This document captures current context for VP orchestration work in UA, the current failure state, and the next implementation steps.  
Goal: hand off to a coding agent to implement a robust, reusable VP operating model for Simone (for both CODIE and Generalist VP lanes) without prompt bloat.

## Scope of Work in Progress
1. Diagnose why Simone failed to reliably use VP General in session:
- `AGENT_RUN_WORKSPACES/session_20260220_152837_6a644b94`
2. Validate behavior of external VP workspace roots:
- `AGENT_RUN_WORKSPACES/vp_general_primary_external`
- `AGENT_RUN_WORKSPACES/vp_coder_primary_external`
3. Define architecture for scalable agent education pattern:
- Tool-first contracts
- Skill-based operating procedures
- Minimal prompt additions

## Current State (As-Is)
1. VP infrastructure is partially implemented:
- External VP profiles exist (`vp.coder.primary`, `vp.general.primary`)
- Worker loop and mission queue model exist
- Dashboard VP panel exists
2. Simone does not have a first-class internal VP tool contract to operate this lane.
3. In the failed run, Simone improvised by searching code and issuing direct curl/bash calls.
4. VP external workspace roots remained effectively empty; output was generated in Simone session workspace.
5. Significant backend reliability issues are present:
- `sqlite3.OperationalError: database is locked` during mission dispatch path
- `500` on VP mission listing due datetime subtraction bug (`naive` vs `aware` datetimes)

## Confirmed Root Causes
1. Missing first-class VP tools in internal registry:
- `src/universal_agent/tools/internal_registry.py`
- No `vp_dispatch_mission` / `vp_get_mission` / `vp_list_missions` / etc.
2. Operational coupling issue:
- VP dispatch endpoints currently use gateway runtime DB connection path, contributing to lock contention under concurrent load.
3. API serialization bug:
- `src/universal_agent/gateway_server.py` duration calculation can subtract mixed datetime types and crash endpoint.
4. Behavior-design issue:
- Current approach relies on implicit knowledge; Simone lacks a deterministic happy-path contract for external VP operations.

## Design Decision (Approved Direction)
1. Use one dedicated VP DB for all external VPs:
- `vp_state.db` for mission queue/events/sessions
- Keep `runtime_state.db` for core UA runtime/session concerns
2. Use in-process internal MCP tools for VP control:
- Do not rely on shell/curl endpoint calls from Simone
3. Use one reusable skill for operating procedure:
- Keep prompt changes thin and non-bloated
4. Keep one mission-control interface for both CODIE and Generalist:
- Distinguish with `vp_id`, not different orchestration patterns

## Why One Shared VP DB (Not one DB per VP now)
1. Unified mission ledger for Simone and dashboard visibility.
2. Lower complexity and faster stabilization.
3. Easier cancellation/reporting/event correlation across multiple external VPs.
4. Per-VP DB sharding can be added later if throughput/isolation demands it.

## Next Implementation Target
Primary target: make VP orchestration deterministic, observable, and reusable.

### P0 — Reliability first
1. Add `vp_state.db` and route VP endpoints/workers to it.
2. Fix datetime duration crash in VP mission serialization.
3. Improve lock handling and retry strategy for VP dispatch writes.
4. Return actionable error responses for transient lock conditions.

### P0 — Tool-first VP contract
1. Add internal tools:
- `vp_dispatch_mission`
- `vp_get_mission`
- `vp_list_missions`
- `vp_wait_mission`
- `vp_cancel_mission`
- `vp_read_result_artifacts`
2. Register in internal toolkit registry.
3. Ensure both CODIE and Generalist use same control-plane semantics via `vp_id`.

### P1 — Skill + thin prompt
1. Add `vp-orchestration` skill describing required VP operating behavior.
2. Add minimal prompt rule:
- “Use `vp_*` tools for external primary-agent execution; do not call VP HTTP endpoints directly.”

### P1 — Bidirectional communication parity
1. Ensure mission lifecycle events (dispatched/started/completed/failed/cancelled) are visible in Simone context and dashboard.
2. Ensure artifact references are exposed consistently for both VP lanes.

## Exact Code Touchpoints (Planned)
1. `src/universal_agent/durable/db.py`
2. `src/universal_agent/gateway.py`
3. `src/universal_agent/gateway_server.py`
4. `src/universal_agent/vp/dispatcher.py`
5. `src/universal_agent/vp/worker_main.py`
6. `src/universal_agent/vp/worker_loop.py`
7. `src/universal_agent/tools/internal_registry.py`
8. `src/universal_agent/tools/` (new VP tool module)
9. `src/universal_agent/prompt_builder.py` (thin rule only)
10. `.agents/skills/` (new `vp-orchestration` skill)
11. `tests/gateway/`
12. `tests/durable/`
13. `tests/integration/`

## Acceptance Criteria
1. Simone can dispatch VP General and CODIE via internal `vp_*` tools only.
2. No ad-hoc curl/bash endpoint probing is needed for normal operation.
3. VP mission API endpoints no longer 500 on duration serialization.
4. VP dispatch works under concurrent runtime activity without lock-failure cascades.
5. Mission output appears in VP workspace paths and is retrievable via `vp_read_result_artifacts`.
6. Dashboard and chat activity reflect mission lifecycle consistently.

## Handoff Notes for Coding Agent
1. Prioritize P0 reliability before any prompt/skill refinement.
2. Do not add large new prompt blocks; implement tool contract first.
3. Keep changes backward-compatible where possible, but prefer correctness over preserving broken behavior.
4. Validate using targeted integration run:
- Dispatch simple poem/file mission to `vp.general.primary`
- Confirm completed status + artifact in VP workspace
- Repeat for CODIE with allowed target path

## Relevant Evidence Paths
1. Failed run workspace:
- `AGENT_RUN_WORKSPACES/session_20260220_152837_6a644b94`
2. VP external roots checked:
- `AGENT_RUN_WORKSPACES/vp_general_primary_external`
- `AGENT_RUN_WORKSPACES/vp_coder_primary_external`
3. Runtime logs:
- `gateway.log`
