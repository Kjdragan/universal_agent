# 045 Technical Architecture Overview

**Date:** 2026-02-03  
**Scope:** Current Universal Agent (UA) architecture and runtime data flow.

---

## 1) System Surfaces

- **CLI** (`src/universal_agent/main.py`)
  - Fastest dev loop; uses the in-process execution engine.
- **Gateway API** (`src/universal_agent/gateway_server.py`)
  - FastAPI server exposing sessions, streaming WS, heartbeat, cron, and ops endpoints.
- **Web UI** (`web-ui/`)
  - Browser client using gateway WS + REST.
  - Includes an Ops panel for sessions/logs/skills/config.
- **(Telegram)**
  - De-prioritized for now; not included in this document.

---

## 2) Core Execution Engine

- **Execution Engine:** `ProcessTurnAdapter` (in `src/universal_agent/execution_engine.py`)
- **Gateway Abstraction:** `InProcessGateway` in `src/universal_agent/gateway.py`
  - `create_session()` creates workspace, adapter, session.
  - `execute()` streams `AgentEvent` over WS.
  - `run_query()` returns a single `GatewayResult`.

---

## 3) Runtime Control Planes

### 3.1 Heartbeat Service
- **Entry:** `HeartbeatService` (`src/universal_agent/heartbeat_service.py`)
- **Endpoint:**
  - `POST /api/v1/heartbeat/wake`
  - `GET /api/v1/heartbeat/last`
- **Storage:** `AGENT_RUN_WORKSPACES/<session>/heartbeat_state.json`
- **Behavior:** interval-based runs, active-hours windows, delivery policies.

### 3.2 Cron Service
- **Entry:** `CronService` (`src/universal_agent/cron_service.py`)
- **Endpoints:**
  - `GET/POST /api/v1/cron/jobs`
  - `GET/PUT/DELETE /api/v1/cron/jobs/{job_id}`
  - `POST /api/v1/cron/jobs/{job_id}/run`
  - `GET /api/v1/cron/jobs/{job_id}/runs`
  - `GET /api/v1/cron/runs`
- **Storage:**
  - `AGENT_RUN_WORKSPACES/cron_jobs.json`
  - `AGENT_RUN_WORKSPACES/cron_runs.jsonl`

### 3.3 Ops Control Plane
- **Entry:** `ops_config.json` (`AGENT_RUN_WORKSPACES/ops_config.json`)
- **Endpoints:**
  - `GET/POST/PATCH /api/v1/ops/config`
  - `GET /api/v1/ops/sessions` + session actions
  - `GET /api/v1/ops/logs/tail`
  - `GET /api/v1/ops/skills` + `PATCH /api/v1/ops/skills/{skill_key}`
  - `GET /api/v1/ops/channels` + `POST /api/v1/ops/channels/{id}/logout`
- **Security:** Optional `UA_OPS_TOKEN` bearer or `x-ua-ops-token` header.

---

## 4) Event Flow (Websocket)

**Gateway WS:** `/api/v1/sessions/{session_id}/stream`

Key event types:
- `connected`
- `text`, `tool_call`, `tool_result`, `work_product`
- `heartbeat_summary`, `heartbeat_indicator`
- `system_event`, `system_presence`

---

## 5) Workspace Layout (Data Persistence)

Each session is a workspace under `AGENT_RUN_WORKSPACES/`.

Typical files:
- `run.log`
- `activity_journal.log`
- `heartbeat_state.json`
- `memory/*` and `MEMORY.md` (memory system)
- `work_products/` (artifacts)

Global:
- `cron_jobs.json`, `cron_runs.jsonl`
- `ops_config.json`
- `runtime_state.db`

---

## 6) Security + Guardrails

- **Allowlist:** `ALLOWED_USER_IDS` (gateway enforces for sessions + WS).
- **Ops Token:** `UA_OPS_TOKEN` for `/api/v1/ops/*` endpoints.
- **Feature flags:**
  - `UA_ENABLE_HEARTBEAT`, `UA_ENABLE_CRON`
  - `UA_MEMORY_ENABLED`, `UA_MEMORY_INDEX` (memory subsystem)

---

## 7) Summary

UA runs an in-process execution engine behind a Gateway API, with Web UI and Ops control plane layered on top. Heartbeats and cron are gateway-local services with workspace-backed persistence. Operational controls are explicit and optional via an ops config file and token-gated endpoints.
