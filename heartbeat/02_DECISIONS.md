# 02. Decisions — Heartbeat Project

This is a running log of decisions. Append new decisions (do not rewrite history unless correcting a factual error).

## Decision Log

### D-001 — Heartbeat lives in the gateway (not CLI direct)
- **Decision**: The proactive heartbeat scheduler should run in **gateway mode** as a background service.
- **Rationale**:
  - CLI direct runs are optimized for speed and typically create a new workspace per run.
  - Heartbeat needs a stable session/workspace and a push-based event stream.
- **Evidence**:
  - UA gateway server lifecycle exists (`src/universal_agent/gateway_server.py`).
  - UA already has a unified engine adapter (`ProcessTurnAdapter`).

### D-002 — Heartbeat should emit first-class events (not only log lines)
- **Decision**: Heartbeat should emit a dedicated event shape into the UA event stream.
- **Rationale**:
  - UI surfaces (web UI, future Telegram/Slack) need explicit status and suppression semantics.
  - Re-using `STATUS` strings alone makes it hard to build stable UI behavior.
- **Note**: This likely requires adding a new `EventType` (or a stable `STATUS.kind`).

### D-003 — HEARTBEAT.md is the primary “safe checklist” surface
- **Decision**: Use a per-workspace `HEARTBEAT.md` as the stable, small checklist prompt input.
- **Rationale**:
  - Mirrors Clawdbot behavior.
  - Keeps prompt bloat controlled and makes heartbeat behavior explicit/auditable.

### D-004 — Heartbeat must have an explicit suppression contract
- **Decision**: Introduce an explicit `HEARTBEAT_OK` (or `UA_HEARTBEAT_OK`) token contract to suppress no-op heartbeats.
- **Rationale**:
  - Prevents spam.
  - Enables consistent “no alert” behavior while still logging/indicating health.

### D-005 — Heartbeat uses a first‑class EventType
- **Decision**: Add `EventType.HEARTBEAT` (and mirror in WebSocket `EventType`) rather than overloading `STATUS.kind`.
- **Rationale**:
  - Cleaner UI routing and stable contracts across CLI/gateway/Web UI.
  - Avoids ambiguous parsing of status text.

### D-006 — Heartbeat defaults off + kill switch
- **Decision**: Heartbeat is **disabled by default** and gated by a config flag, with a proposed env kill switch (`UA_DISABLE_HEARTBEAT`).
- **Rationale**:
  - Minimizes latency risk until behavior is proven.
  - Aligns with deployment realities (uptime vs scale-to-zero).

