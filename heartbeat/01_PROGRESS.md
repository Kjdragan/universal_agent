# 01. Progress — Heartbeat Project

## Status
- **State**: Design phase (no implementation in this repo yet)
- **Last updated**: 2026-01-28

## What we reviewed (evidence)
### 1) Real UA execution run (CLI direct)
We reviewed the run artifacts for:
- `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260128_234441`

Key files observed:
- `transcript.md`
- `trace.json`
- `run.log`
- `tasks/russia_ukraine_war_jan2026/*`
- `work_products/report.html`
- `work_products/russia_ukraine_war_report.pdf`

Implications for heartbeat:
- UA already produces **durable artifacts** per run (`trace.json`, `transcript.md`, `work_products/*`).
- Heartbeat should integrate with the same artifact/trace model so proactive turns remain auditable.
- In **CLI direct**, each query typically creates a new workspace; heartbeat is more naturally aligned with **gateway-mode sessions** that persist across multiple turns.

### 2) UA gateway + event stream model
We reviewed:
- `src/universal_agent/agent_core.py` (`AgentEvent`, `EventType`)
- `src/universal_agent/execution_engine.py` (`ProcessTurnAdapter` emits `AgentEvent`s)
- `src/universal_agent/gateway.py` (`InProcessGateway` uses `ProcessTurnAdapter`)
- `src/universal_agent/gateway_server.py` (FastAPI lifespan + WS streaming)

Key implication:
- UA already has a **canonical place** to emit heartbeat events: the gateway event stream (`AgentEvent`).

## Current deliverables (docs)
- `03_UA_Heartbeat_Schema_and_Event_Model.md`
- `04_Gateway_Scheduler_Prototype_Design.md`
- `05_Clawdbot_Heartbeat_System_Report.md`

## Next milestones
### Milestone A — Agree on MVP semantics
- Define the **heartbeat prompt contract** (including an explicit “OK token” and suppression rule).
- Decide whether heartbeat is:
  - **Per session** (recommended), or
  - **Per user** (global across sessions)

### Milestone B — Prototype in gateway lifecycle
- Implement a minimal scheduler integrated into `gateway_server` lifespan.
- Add server push/broadcast support so heartbeats can stream without a user initiating a request.

### Milestone C — Delivery targets
- Decide what “target: last” means for UA (especially with upcoming Telegram/Slack surfaces).
- Add per-channel visibility controls (OK/alerts/indicator).

## Risks / watch-outs
- **WebSocket model**: today, UA gateway server streams events only in response to an incoming `execute` request on that socket. A proactive heartbeat needs a **push/broadcast path**.
- **Session semantics**: in UA, “session” is currently strongly tied to a **workspace directory**. Heartbeat needs a stable session/workspace to avoid constantly re-initializing context.
- **Cost**: frequent heartbeats can burn tokens; we need explicit skip rules (empty `HEARTBEAT.md`, quiet hours, dedupe).

