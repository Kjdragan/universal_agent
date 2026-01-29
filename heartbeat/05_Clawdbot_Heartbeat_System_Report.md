---
title: "Clawdbot Heartbeat System Report (and UA implications)"
status: draft
last_updated: 2026-01-28
---

# 05. Clawdbot Heartbeat System Report (and UA implications)

## 1. Why this document exists
This document summarizes how the **Clawdbot/Moltbot heartbeat system** works and what it implies for implementing an analogous system in Universal Agent.

This version adds UA-specific observations from a real UA run and from the UA gateway/event model.

## 2. Summary (one-page view)
Heartbeat in Clawdbot is a **periodic, context-aware agent turn** that runs in the **main session**, reads a lightweight `HEARTBEAT.md` checklist (if present), and decides whether to deliver a message or stay quiet.

It is intentionally **not cron**: it trades exact timing for batching, context, and spam suppression.

Key mechanisms:
- **Scheduler**: timer-driven loop that wakes when due.
- **Wake coalescer**: coalesces interval/manual triggers; retries when busy.
- **Session + delivery targeting**: resolve session context + where to deliver.
- **Prompt + response contract**: `HEARTBEAT_OK` + `ackMaxChars` suppression.
- **Visibility policy**: per-channel/per-account flags (`showOk`, `showAlerts`, `useIndicator`).
- **Event emission**: publishes heartbeat status into the gateway event stream.

## 3. Clawdbot heartbeat architecture
### 3.1 Core files (code + docs)
- Runner + scheduler: `src/infra/heartbeat-runner.ts` @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#1-902
- Wake coalescer: `src/infra/heartbeat-wake.ts` @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-wake.ts#1-71
- Prompt + token logic: `src/auto-reply/heartbeat.ts` @/home/kjdragan/lrepos/clawdbot/src/auto-reply/heartbeat.ts#1-140
- Visibility controls: `src/infra/heartbeat-visibility.ts` @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-visibility.ts#1-74
- Delivery target resolution: `src/infra/outbound/targets.ts` @/home/kjdragan/lrepos/clawdbot/src/infra/outbound/targets.ts#1-315
- Events: `src/infra/heartbeat-events.ts` @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-events.ts#1-58
- Gateway wiring: `src/gateway/server.impl.ts` @/home/kjdragan/lrepos/clawdbot/src/gateway/server.impl.ts#394-493

### 3.2 Important behavioral details (Clawdbot)
- **Busy gating**: if the main command lane has requests in flight, heartbeat is skipped as `requests-in-flight` and retried.
- **Empty HEARTBEAT.md skip**: if `HEARTBEAT.md` exists but is effectively empty, heartbeat skips to save tokens.
- **Deduplication**: suppress duplicate “same message again” heartbeats for up to 24h.

## 4. UA-specific observations (from your run + code)
### 4.1 UA already has durable per-run artifacts
From `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260128_234441`:
- `trace.json` (structured tool call timeline)
- `transcript.md` (human-readable replay)
- `run.log` (captured console log)
- `tasks/...` and `work_products/...`

This strongly suggests UA heartbeat should:
- Produce the same durable artifacts for every heartbeat run.

### 4.2 UA already has a gateway event model
UA has:
- `AgentEvent` and `EventType` in `src/universal_agent/agent_core.py`
- `ProcessTurnAdapter` in `src/universal_agent/execution_engine.py` which emits `AgentEvent`s
- `InProcessGateway` in `src/universal_agent/gateway.py` which uses `ProcessTurnAdapter`
- `gateway_server.py` which exposes FastAPI + WebSocket streaming

This makes heartbeat feasible without introducing a second execution engine.

### 4.3 Key UA mismatch vs Clawdbot: proactive push
Clawdbot broadcasts heartbeat events to connected clients.

UA `gateway_server.py` currently streams events only after a client sends an `execute` message.

Therefore, a UA heartbeat implementation will likely need:
- a **server push / broadcast** mechanism, at least for “heartbeat summary” events.

## 5. Feasibility assessment (UA)
Implementing a Clawdbot-like heartbeat in UA is feasible, with two non-trivial integration points:
- **Session model**: heartbeat wants a stable long-lived session; UA CLI direct currently uses a fresh workspace per run.
- **Push delivery**: UA gateway needs a way to push heartbeat events proactively.

## 6. Recommended UA approach (high-level)
- Use `HEARTBEAT.md` in each session workspace as the checklist.
- Introduce an explicit suppression token (e.g. `UA_HEARTBEAT_OK`).
- Add a scheduler in the gateway server lifecycle.
- Emit a stable “heartbeat summary” event into the UA event stream.

## Appendix — Key Clawdbot docs
- @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#1-298
- @/home/kjdragan/lrepos/clawdbot/docs/automation/cron-vs-heartbeat.md#1-275
