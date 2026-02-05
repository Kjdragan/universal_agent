---
title: "Clawdbot Heartbeat System Report"
status: draft
last_updated: 2026-01-28
---

# 31. Clawdbot Heartbeat System Report

## 1. Why this document exists
This document summarizes how the **Clawdbot/Moltbot heartbeat system** works, how it is wired across the gateway/runtime, and how its behavior could be adapted for the Universal Agent. It focuses on architecture, flows, configuration, and feasibility for a proactive, context-aware agent loop.

## 2. Summary (one-page view)
Heartbeat in Clawdbot is a **periodic, context-aware agent turn** that runs in the **main session**, reads a lightweight `HEARTBEAT.md` checklist (if present), and decides whether to deliver a message or stay quiet. It is **not a cron substitute**: it trades exact timing for batching, context, and spam suppression. The system includes:

- **Scheduler**: A timer-driven loop that wakes the heartbeat when due.
- **Wake coalescer**: Coalesces manual/interval triggers and retries when the main queue is busy.
- **Session + delivery targeting**: Resolves the main session, sender identity, and delivery channel/recipient.
- **Prompt + response contract**: Default prompt + `HEARTBEAT_OK` acknowledgment token with max-ack-char suppression.
- **Visibility policy**: Per-channel/per-account controls for showing OKs, alerts, or UI indicators.
- **Event emission**: Broadcasts heartbeat status into the gateway event stream.

The system is a **lightweight proactive loop**, designed to surface actionable items without spamming the user.

## 3. Clawdbot heartbeat architecture
### 3.1 Core files (code + docs)
- **Runner + scheduler**: `src/infra/heartbeat-runner.ts` (run loop + scheduler). @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#1-902
- **Wake coalescer**: `src/infra/heartbeat-wake.ts`. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-wake.ts#1-71
- **Prompt + token logic**: `src/auto-reply/heartbeat.ts`. @/home/kjdragan/lrepos/clawdbot/src/auto-reply/heartbeat.ts#1-140
- **Visibility controls**: `src/infra/heartbeat-visibility.ts`. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-visibility.ts#1-74
- **Delivery target resolution**: `src/infra/outbound/targets.ts`. @/home/kjdragan/lrepos/clawdbot/src/infra/outbound/targets.ts#1-315
- **Events**: `src/infra/heartbeat-events.ts`. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-events.ts#1-58
- **Gateway wiring**: `src/gateway/server.impl.ts` (start runner + event broadcast). @/home/kjdragan/lrepos/clawdbot/src/gateway/server.impl.ts#394-493

### 3.2 Architectural responsibilities
1. **Scheduler/Runner**
   - Maintains per-agent heartbeat state (intervals, next due times). @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#757-886
   - Uses `setTimeout` to wake at the earliest next-due timestamp, then dispatches runs across configured agents.

2. **Wake Coalescer**
   - Receives manual wake requests (`requestHeartbeatNow`), coalesces rapid triggers, and retries when busy. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-wake.ts#1-71
   - If the main queue is busy (`requests-in-flight`), it retries with short delay to avoid contention. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#455-458

3. **Config + Session Resolution**
   - Heartbeat configuration is part of agent defaults and per-agent overrides. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#192-358
   - Supports session scoping: main/global/specified session keys. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#311-358

4. **Prompt + Response Contract**
   - Default prompt: read `HEARTBEAT.md` if present, follow it, return `HEARTBEAT_OK` if nothing needs attention. @/home/kjdragan/lrepos/clawdbot/src/auto-reply/heartbeat.ts#1-47
   - `HEARTBEAT_OK` token is stripped at start/end and used to suppress low-value replies. @/home/kjdragan/lrepos/clawdbot/src/auto-reply/heartbeat.ts#49-139

5. **Delivery + Suppression**
   - Resolve delivery target: last channel, explicit channel, or none. @/home/kjdragan/lrepos/clawdbot/src/infra/outbound/targets.ts#174-258
   - Silence OK acknowledgments by default; show alerts; use indicator events for UI. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-visibility.ts#1-72

6. **Event Stream Integration**
   - Heartbeat status events (sent/skipped/failed) are broadcast into the gateway event stream. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-events.ts#1-58

## 4. How the heartbeat run works (step-by-step)
### 4.1 Scheduling
- `startHeartbeatRunner` builds a per-agent schedule (intervals, next due) and arms a timer. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#757-845
- The timer triggers `requestHeartbeatNow({ reason: "interval" })`. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#784-802

### 4.2 Wake coalescing
- `requestHeartbeatNow` schedules a wake; multiple requests coalesce into a single run. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-wake.ts#59-62
- If a run is already in flight, a retry is scheduled. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-wake.ts#24-46

### 4.3 Run gating
Before calling the model:
- Heartbeat is enabled and interval is valid. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#437-447
- Active hours check passes. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#450-452
- Main queue is idle (otherwise skip + retry). @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#455-458
- If `HEARTBEAT.md` exists and is effectively empty, skip to save tokens (unless processing exec events). @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#460-475

### 4.4 Session + delivery targeting
- Session key resolved (main/global/specific). @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#481-483
- Delivery target resolved (last channel, explicit channel, or none). @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#483-492

### 4.5 Prompt selection
- Default prompt or custom `heartbeat.prompt`. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#502-509
- If an exec completion event is pending, uses a special prompt instructing the model to relay the output. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#95-99

### 4.6 Response normalization + suppression
- Response payloads are parsed; `HEARTBEAT_OK` is stripped and used to suppress low-value content. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#546-606
- Duplicate message suppression: if the same text repeats within 24h, skip. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#611-639

### 4.7 Delivery + events
- If deliverable and allowed, outbound payloads are sent. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#700-717
- Heartbeat events are emitted (sent/skipped/failed). @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#733-753

## 5. Clawdbot heartbeat configuration surface
### 5.1 Agent defaults + per-agent overrides
Heartbeat is configured under `agents.defaults.heartbeat` and optional `agents.list[].heartbeat` overrides. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#192-273

Key fields (from docs):
- `every`: interval (duration string). Default 30m. @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#39-43
- `prompt`: default prompt or override. @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#41-44
- `target`: `last | none | <channel>` (delivery). @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#145-148
- `to`: recipient override. @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#145-149
- `activeHours`: quiet hours window. @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#46-47
- `includeReasoning`: optional separate reasoning message. @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#140-141
- `ackMaxChars`: max chars after `HEARTBEAT_OK` before delivery. @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#67-68

### 5.2 Visibility controls
Channel- and account-level settings determine whether OKs, alerts, or indicator events are sent. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-visibility.ts#1-72

## 6. HEARTBEAT.md behavior
- The default prompt instructs the model to read `HEARTBEAT.md` and follow it. @/home/kjdragan/lrepos/clawdbot/src/auto-reply/heartbeat.ts#1-47
- If the file exists and is effectively empty, the heartbeat run is skipped. @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#460-475
- A template file exists for bootstrapping. @/home/kjdragan/lrepos/clawdbot/docs/reference/templates/HEARTBEAT.md#1-10

## 7. How this maps to Universal Agent
### 7.1 Current Universal Agent capabilities relevant to heartbeat
- **Gateway** provides a single execution path for multiple clients and can host background behaviors. @/home/kjdragan/lrepos/universal_agent/Project_Documentation/016_Execution_Engine_Gateway_Model.md#7-215
- **Durability + memory** already exist (SQLite + Letta memory). @/home/kjdragan/lrepos/universal_agent/Project_Documentation/03_Architecture/System_Overview.md#1-70; @/home/kjdragan/lrepos/universal_agent/Project_Documentation/03_Architecture/Memory_System.md#1-66
- **Event streaming** is part of the gateway model; hooks can emit statuses. @/home/kjdragan/lrepos/universal_agent/AGENTS.md#224-251
- **URW** manages long-running execution and compaction (useful for heartbeat loops). @/home/kjdragan/lrepos/universal_agent/Project_Documentation/04_Subsystems/URW_Wrapper.md#1-29

### 7.2 Gaps (what heartbeat adds)
- **No periodic proactive turn** in the current Universal Agent.
- **No scheduled wake coalescer** or timing loop for proactive nudges.
- **No heartbeat-specific prompt contract** (e.g., `HEARTBEAT_OK`).
- **No system event pipeline** for “exec finished” wakeups.
- **No per-channel visibility policy** around heartbeat content.

## 8. Feasibility assessment
Implementing a heartbeat in Universal Agent is **highly feasible** because:
- The gateway already centralizes execution, so a background scheduler can sit adjacent to the gateway process.
- The execution engine (CLI path) can already run agent turns; heartbeat is “just another turn” with a different prompt and metadata.
- The system already has a durable storage layer for session state and a memory system to supply context.

Main integration challenges:
- **Session routing + delivery**: decide how to resolve “last channel” vs explicit channel for Telegram/Slack.
- **Output suppression**: implement `HEARTBEAT_OK` stripping to reduce noise.
- **Background lifecycle**: ensure scheduler runs only when gateway is alive and is safe with multiple sessions.
- **Concurrency**: avoid running heartbeat when the main queue is busy (similar to Clawdbot’s `requests-in-flight` check).

## 9. Implementation approach for Universal Agent (proposal)
### 9.1 Minimum viable heartbeat (Phase 1)
1. **Heartbeat config**
   - Add `agents.defaults.heartbeat` + optional `agents.list[].heartbeat` similar to Clawdbot.
   - Include: `every`, `prompt`, `target`, `to`, `activeHours`, `ackMaxChars`, `includeReasoning`.

2. **Scheduler + wake coalescer**
   - Add a lightweight scheduler inside the gateway server process.
   - Implement `requestHeartbeatNow()` and a coalescer to avoid rapid duplicate runs.

3. **Heartbeat prompt contract**
   - Introduce a `HEARTBEAT_OK` token + stripping behavior.
   - If content is empty/short, suppress delivery and emit a heartbeat event only.

4. **HEARTBEAT.md checklist**
   - In each workspace, allow optional `HEARTBEAT.md`.
   - If file exists and is effectively empty, skip heartbeat to save tokens.

5. **Gateway event stream**
   - Emit heartbeat events in the gateway stream for UI visibility.

### 9.2 Delivery & channel targeting (Phase 2)
1. **Delivery resolution**
   - Add a delivery target resolver that supports:
     - `last`: last channel used in a session.
     - explicit channel (`telegram`, `slack`, `webchat`).
     - `none`: no delivery, run-only.

2. **Visibility policy**
   - Add per-channel visibility flags: `showOk`, `showAlerts`, `useIndicator`.

3. **Deduplication**
   - Track last heartbeat text per session to avoid duplicates within a time window.

### 9.3 Exec event integration (Phase 3)
- Add a system-event queue to the main session (if not already in place).
- Use a special heartbeat prompt for “exec finished” events to relay results.

## 10. Proposed data + file layout (UA)
- `Project_Documentation/` (this report + future heartbeat design docs)
- Workspace file: `HEARTBEAT.md`
- Optional: `heartbeat-state.json` for last-run metadata (dedupe, last delivery)

## 11. Risks & mitigations
| Risk | Mitigation |
| --- | --- |
| Too many “no-op” heartbeats | Skip when `HEARTBEAT.md` is empty; suppress `HEARTBEAT_OK`. |
| Overlap with active user sessions | Check active queue; coalesce + retry. |
| Multi-channel spam | Per-channel visibility settings; per-agent configuration. |
| Cost growth | Default 30m interval; keep `HEARTBEAT.md` tiny; allow cheaper model. |

## 12. Recommended next steps
1. Confirm the desired heartbeat prompt + token contract for Universal Agent.
2. Decide where session routing and “last channel” metadata will live (gateway session store).
3. Define heartbeat config schema in UA configs (mirroring Clawdbot).
4. Implement a small prototype scheduler in the gateway server and validate with one workspace.

---

## Appendix A — Key Clawdbot references
- Heartbeat docs: @/home/kjdragan/lrepos/clawdbot/docs/gateway/heartbeat.md#1-298
- Cron vs Heartbeat: @/home/kjdragan/lrepos/clawdbot/docs/automation/cron-vs-heartbeat.md#1-275
- Heartbeat runner: @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-runner.ts#1-902
- Wake coalescer: @/home/kjdragan/lrepos/clawdbot/src/infra/heartbeat-wake.ts#1-71
- Prompt + token: @/home/kjdragan/lrepos/clawdbot/src/auto-reply/heartbeat.ts#1-140
