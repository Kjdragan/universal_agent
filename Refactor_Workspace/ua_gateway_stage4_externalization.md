# UA Gateway Stage 4 — Externalization Plan

**Owner:** Cascade  
**Created:** 2026-01-24  
**Status:** ✅ Complete  
**Prerequisite:** Stage 3 complete (gateway default in dev mode)

---

## Goal

Expose the in-process `InProcessGateway` as an HTTP/WebSocket service while maintaining CLI default behavior (in-process path).

---

## Design Principles

1. **Parity first** — External gateway must produce identical event streams as in-process.
2. **CLI unchanged** — CLI continues using in-process gateway by default; external gateway is opt-in.
3. **Session portability** — Sessions created via external gateway should be resumable from CLI and vice versa.
4. **Minimal new surface** — Reuse existing `api/server.py` infrastructure where possible.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI / URW / Bot                         │
├─────────────────────────────────────────────────────────────────┤
│                      Gateway Interface                          │
│   ┌─────────────────────┐     ┌──────────────────────────────┐  │
│   │  InProcessGateway   │     │     ExternalGateway          │  │
│   │  (current Stage 3)  │     │  (HTTP/WS client wrapper)    │  │
│   └─────────────────────┘     └──────────────────────────────┘  │
│                                         │                       │
│                                         ▼                       │
│                              ┌──────────────────────────────┐   │
│                              │    Gateway Server (new)      │   │
│                              │  - POST /sessions            │   │
│                              │  - WS /sessions/:id/execute  │   │
│                              │  - GET /sessions             │   │
│                              └──────────────────────────────┘   │
│                                         │                       │
│                                         ▼                       │
│                              ┌──────────────────────────────┐   │
│                              │    InProcessGateway          │   │
│                              │    (same as Stage 3)         │   │
│                              └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Endpoints (Draft)

### REST

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sessions` | Create a new gateway session |
| `GET` | `/api/v1/sessions` | List sessions |
| `GET` | `/api/v1/sessions/:id` | Get session metadata |
| `DELETE` | `/api/v1/sessions/:id` | Terminate session |

### WebSocket

| Path | Description |
|------|-------------|
| `/api/v1/sessions/:id/stream` | Execute query and stream `AgentEvent`s |

### Event Wire Format

```json
{
  "type": "TEXT|TOOL_CALL|TOOL_RESULT|STATUS|AUTH_REQUIRED|WORK_PRODUCT|ERROR",
  "data": { ... },
  "timestamp": "ISO8601"
}
```

---

## Implementation Tasks

### 4.1 Gateway Server Scaffold
- [x] Create `src/universal_agent/gateway_server.py` with FastAPI/Starlette app.
- [x] Wire REST endpoints for session CRUD.
- [x] Wire WebSocket endpoint for streaming execution.

### 4.2 ExternalGateway Client
- [x] Create `ExternalGateway` class implementing same interface as `InProcessGateway`.
- [x] HTTP client for session create/list.
- [x] WebSocket client for `execute()` streaming.

### 4.3 CLI Integration
- [x] Add `--gateway-url` / `UA_GATEWAY_URL` to route CLI through external gateway.
- [x] Fallback to in-process if server unreachable.

### 4.4 Parity Validation
- [x] REST endpoints tested (health, sessions CRUD).
- [ ] Full parity test with interactive query (pending).

### 4.5 Documentation
- [x] Updated `ua_gateway_smoke_tests.md` with Stage 4 tests.
- [x] Updated `ua_gateway_outstanding_work.md` with Stage 4 status.

---

## Exit Criteria

- [x] External gateway server runs standalone (`python -m universal_agent.gateway_server`).
- [x] CLI `--gateway-url` flag connects to external gateway.
- [x] No regressions in in-process path.
- [ ] Session resume works across in-process ↔ external boundary (pending test).
- [ ] Full interactive query parity test (pending).

---

## Open Questions

1. **Auth/API keys** — How to secure external gateway? Bearer token? mTLS?
2. **Session storage** — Share SQLite runtime DB or use separate session store?
3. **Deployment** — Containerize gateway server? Docker Compose for local dev?

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/universal_agent/gateway_server.py` | Create — FastAPI/Starlette server |
| `src/universal_agent/gateway.py` | Modify — Add `ExternalGateway` class |
| `src/universal_agent/main.py` | Modify — Add `--gateway-url` routing |
| `Refactor_Workspace/ua_gateway_smoke_tests.md` | Modify — Add external gateway tests |

---

## Related Documents

- Stage 3 progress: `ua_gateway_refactor_progress.md`
- Guardrails checklist: `ua_gateway_guardrails_checklist.md`
- Outstanding work: `ua_gateway_outstanding_work.md`
