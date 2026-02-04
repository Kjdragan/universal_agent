# 046 Functionality Catalog

**Date:** 2026-02-03  
**Scope:** Current feature inventory with configuration notes.

---

## 1) Core Agent Execution

- **Gateway Sessions**
  - Create/resume/execute sessions via Gateway API.
  - Stream events over WS.
- **Execution Engine**
  - `ProcessTurnAdapter` for tool orchestration and run lifecycle.

Config:
- `ALLOWED_USER_IDS` (allowlist)
- `UA_GATEWAY_PORT`

---

## 2) Memory System (Phase 1)

- File-backed memory (`MEMORY.md` + `memory/` files).
- Index support (JSON/FTS modes).
- Pre-compaction flush to avoid loss.

Config:
- `UA_MEMORY_ENABLED=1`
- `UA_MEMORY_INDEX=fts|json|off`
- `UA_MEMORY_MAX_TOKENS`

---

## 3) Heartbeat (Phase 2)

- Scheduled heartbeat runs with:
  - Active hours
  - ACK token stripping (`HEARTBEAT_OK`)
  - Delivery modes (`last`, `explicit`, `none`)
  - Visibility controls (showOk/showAlerts/indicator)

Endpoints:
- `POST /api/v1/heartbeat/wake`
- `GET /api/v1/heartbeat/last`

Config (examples):
- `UA_ENABLE_HEARTBEAT=1`
- `UA_HEARTBEAT_INTERVAL=30m`
- `UA_HB_DELIVERY_MODE=explicit`
- `UA_HB_EXPLICIT_SESSION_IDS=CURRENT`

---

## 4) Cron (Phase 2)

- Job CRUD and run history.
- Isolated session per job run.
- Optional heartbeat wake after cron completion.

Endpoints:
- `GET/POST /api/v1/cron/jobs`
- `GET/PUT/DELETE /api/v1/cron/jobs/{job_id}`
- `POST /api/v1/cron/jobs/{job_id}/run`
- `GET /api/v1/cron/jobs/{job_id}/runs`
- `GET /api/v1/cron/runs`

Config:
- `UA_ENABLE_CRON=1`
- `UA_CRON_MOCK_RESPONSE=1` (tests/dev)

---

## 5) System Events + Presence

- System event queue (session-scoped) injected into next run.
- Presence events for runtime monitoring.

Endpoints:
- `POST /api/v1/system/event`
- `GET /api/v1/system/event/{session_id}`
- `POST /api/v1/system/presence`
- `GET /api/v1/system/presence`

---

## 6) Ops Control Plane (Phase 3)

- Sessions list/preview/reset/compact/delete
- Log tail
- Skills enable/disable
- Channels status/logout
- Ops config editor (base-hash safety)

Endpoints:
- `GET /api/v1/ops/sessions`
- `GET /api/v1/ops/sessions/{id}`
- `GET /api/v1/ops/sessions/{id}/preview`
- `POST /api/v1/ops/sessions/{id}/reset`
- `POST /api/v1/ops/sessions/{id}/compact`
- `DELETE /api/v1/ops/sessions/{id}`
- `GET /api/v1/ops/logs/tail`
- `GET /api/v1/ops/skills`
- `PATCH /api/v1/ops/skills/{skill_key}`
- `GET /api/v1/ops/channels`
- `POST /api/v1/ops/channels/{id}/logout`
- `GET/POST/PATCH /api/v1/ops/config`
- `GET /api/v1/ops/models`

Config:
- `UA_OPS_TOKEN` (optional gate)

---

## 7) Web UI

- Real-time event stream (WS)
- Sessions list + previews
- Ops panel for logs/skills/config
- Heartbeat status and system presence

---

## 8) Telegram

- Not part of active test scope (currently unstable).
- No Telegram tests should be run.

---

## 9) Known Warnings

- Logfire 401 warnings indicate token misconfiguration (non-fatal).

---

## 10) Suggested Next Verification

- Run gateway heartbeat/cron/system tests (no Telegram).
- Run ops API tests.
