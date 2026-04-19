# 95. Integration Architectural Decisions (2026-03-06)

## Purpose

This document records deferred architectural decisions identified during the integration review (document 94). These items were assessed as working correctly today but flagged for future revisitation under specific growth conditions.

Each decision includes the observation, current assessment, and the trigger condition that would warrant implementation.

---

## ADR-INT-001: CSI Analytics Session Lanes

**Observation (2.2):** CSI analytics events use hardcoded session-key lanes (`csi_trend_analyst`, `csi_data_analyst`) that are separate from the normal session lifecycle. These lanes lack the same lifecycle controls (timeout, cancel, owner enforcement) as web or Telegram sessions.

**2026-04-19 update:** Superseded. CSI analytics events no longer dispatch through `to_csi_analytics_action()` or hardcoded agent-session lanes. The current direction is passive digest capture plus selected proactive/convergence producers that create Task Hub work only when warranted.

**Trigger for Revisitation:** If CSI analytics work becomes more complex, route selected findings through the current Task Hub/proactive producer path with durable artifacts and explicit scoring, not through the retired per-event hook dispatch path.

**Files involved:**
- `src/universal_agent/signals_ingest.py` — signed ingest validation and YouTube playlist handoff
- `src/universal_agent/gateway_server.py` — CSI action execution pipeline

---

## ADR-INT-002: Auth Surface Unification

**Observation (2.3):** The system has three separate trust/auth models with no shared abstraction:
1. **Dashboard auth:** cookie + HMAC session token with owner resolution
2. **Gateway/ops auth:** `UA_OPS_TOKEN` / `UA_INTERNAL_API_TOKEN` bearer header
3. **CSI ingest auth:** separate shared secret + HMAC signature + timestamp

Each works correctly for its purpose. The dashboard proxy injects ops tokens on behalf of authenticated users, which means the gateway sees an ops-token-authenticated request rather than the original dashboard owner identity.

**Current Assessment:** Workable today. Adding a fourth trust surface would be the trigger for unification.

**Trigger for Revisitation:** If a fourth trusted caller type is added, extract a shared `AuthResult` type and audit-log format that all surfaces emit. This would make cross-surface "who did what" auditing possible without retroactively unifying the auth mechanisms.

**Files involved:**
- `web-ui/lib/dashboardAuth.ts` — cookie auth
- `web-ui/app/api/dashboard/gateway/[...path]/route.ts` — proxy token injection
- `src/universal_agent/api/server.py` — dashboard auth + internal token bypass
- `src/universal_agent/signals_ingest.py` — CSI auth
- `src/universal_agent/gateway_server.py` — ops token paths

---

## ADR-INT-003: Run Workspace Filesystem Isolation

**Observation (2.6):** Run workspaces are created under `AGENT_RUN_WORKSPACES/` with run-based naming plus some legacy session-shaped names during migration. Ownership is enforced at the API level by comparing the authenticated owner with session or run metadata. The filesystem itself has no per-workspace access control — any process with access to `AGENT_RUN_WORKSPACES` can read any workspace's files.

**Current Assessment:** Acceptable for single-user deployment where all processes run under the same OS user.

**Trigger for Revisitation:** If multi-user or multi-tenant access is ever considered, the ownership boundary must move from API-level enforcement to filesystem-level isolation (per-run-workspace directories with restricted permissions, or a workspace-access proxy).

**Files involved:**
- `src/universal_agent/api/server.py` — session ownership enforcement
- `src/universal_agent/gateway_server.py` — session creation
- `scripts/sync_remote_workspaces.sh` — sync has access to all workspaces

---

## What Was Implemented (Not Deferred)

The following items from the integration review (document 94) were implemented rather than deferred:

| # | Item | Implementation |
|---|------|----------------|
| 2.1 | Telegram session model | Added `source: "telegram"` metadata, unique per-query session IDs |
| 2.4 | AgentMail webhook vs WebSocket | Formally deprecated webhook transform with warning log |
| 2.5 | Cross-health surface | CSI delivery health flag added to factory capabilities endpoint |
| 2.7 | Timer fleet visibility | `GET /api/v1/ops/timers` endpoint + Corporation View panel |
| 2.8 | Shared Telegram send | `services/telegram_send.py` — unified async+sync utility |
| 2.9 | Runtime role policy | Added `enable_csi_ingest` and `enable_agentmail` fields |
| 2.10 | Infisical for CSI | `csi_ingester/infisical_bootstrap.py` — optional bootstrap |
