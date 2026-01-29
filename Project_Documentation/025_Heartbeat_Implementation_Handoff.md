# Heartbeat + Memory Implementation Handoff (Phase 0 → Phase 1)

**Date:** 2026-01-29
**Scope:** Heartbeat + Memory + UI parity foundation work in Universal Agent.

---

## 1. Summary (where we are)
We are **at the end of Phase 0** (safety + scaffolding) and ready to begin **Phase 1** (WebSocket broadcast foundation). Phase 0 development changes focused on **feature flags, event contracts, and documentation**, with **no runtime behavior changes** for heartbeat or memory.

The live tracking document is:
- `/home/kjdragan/lrepos/universal_agent/heartbeat/10_Implementation_Plan.md`
  - Phase readiness checklist is the **source of truth** for progress.

The lessons‑learned log is:
- `/home/kjdragan/lrepos/universal_agent/heartbeat/15_Lessons_Learned_and_Plan_Changes.md`

---

## 2. Completed development work (Phase 0)
### 2.1 Heartbeat event contract (first‑class event)
- Added `EventType.HEARTBEAT` to the core event enum.
  - `src/universal_agent/agent_core.py`
- Added `EventType.HEARTBEAT` to WebSocket event enum.
  - `src/universal_agent/api/events.py`

### 2.2 Feature flags and kill switches (no behavior change yet)
- Added a new feature flag module:
  - `src/universal_agent/feature_flags.py`
  - Flags:
    - `UA_ENABLE_HEARTBEAT` / `UA_DISABLE_HEARTBEAT`
    - `UA_ENABLE_MEMORY_INDEX` / `UA_DISABLE_MEMORY_INDEX`
- Wired feature flag **placeholders** into runtime:
  - `src/universal_agent/gateway_server.py`
  - `src/universal_agent/main.py`
  - `src/mcp_server.py`
  - `src/universal_agent/agent_setup.py`
- **Behavior is still unchanged** (everything remains off by default). The flags are in place for Phase 1+.

### 2.3 Decisions recorded
- Decision log updated:
  - `/home/kjdragan/lrepos/universal_agent/heartbeat/02_DECISIONS.md`
  - D‑005: Heartbeat uses first‑class `EventType.HEARTBEAT`.
  - D‑006: Heartbeat defaults off + kill switch.

---

## 3. Key reference documents (read these first)
### Heartbeat project docs
- `/home/kjdragan/lrepos/universal_agent/heartbeat/00_INDEX.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/01_PROGRESS.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/02_DECISIONS.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/03_UA_Heartbeat_Schema_and_Event_Model.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/04_Gateway_Scheduler_Prototype_Design.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/05_Clawdbot_Heartbeat_System_Report.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/10_Implementation_Plan.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/14_Heartbeat_Enablement_Feasibility.md`

### Memory system docs
- `/home/kjdragan/lrepos/universal_agent/heartbeat/11_Clawdbot_Memory_System_Report.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/12_UA_Memory_Feasibility_and_Implementation.md`
- `/home/kjdragan/lrepos/universal_agent/heartbeat/13_Memory_Migration_Checklist.md`

---

## 4. Parity tenet (must not be violated)
All interfaces **must** use the same execution path and produce equivalent results:
- CLI direct
- CLI via gateway
- Web UI
- Telegram

UI differences are **presentation only**, not logic. This is a guiding guardrail in the plan.

---

## 5. What to implement next (Phase 1+)
### Phase 1 — WebSocket broadcast foundation
**Goal:** Session‑wide server push without altering execution logic.
- Extend `ConnectionManager` to track `session_id -> set[WebSocket]`.
- Add `broadcast(session_id, event)` helper.
- Keep existing execute flow unchanged.

Target file:
- `src/universal_agent/gateway_server.py`

### Phase 2 — Heartbeat MVP (scheduler + summary only)
**Goal:** Proactive heartbeat runs with summary events only.
- Add `HeartbeatService` in gateway `lifespan()`.
- Busy gating (skip if session executing) + retry.
- Read `HEARTBEAT.md` (skip if empty).
- Emit heartbeat summary events (new `EventType.HEARTBEAT`).

### Phase 3 — Heartbeat delivery + visibility policy
- Implement delivery modes: `last | explicit | none`.
- Visibility policy: `showOk`, `showAlerts`, `useIndicator`.
- Dedupe window + last message hash.

### Phase 4–6 — Memory system phases
- Phase 4: file scaffolding (`MEMORY.md`, `memory/YYYY-MM-DD.md`) + `ua_memory_get` tool.
- Phase 5: vector index MVP + `ua_memory_search`.
- Phase 6: hybrid search + watchers + optional transcript indexing.

### Phase 7 — Telegram alignment
- Replace Telegram `AgentAdapter` with gateway client path.
- Map `telegram_user_id → gateway_session_id`.
- Ensure heartbeat alerts can be delivered (alerts only by default).

### Phase 8 — Deployment hardening
- Railway topology + persistent storage for `AGENT_RUN_WORKSPACES` + `runtime.db`.
- Long‑running uptime assumption for heartbeat.

---

## 6. Files touched (for context)
- `src/universal_agent/agent_core.py` (EventType)
- `src/universal_agent/api/events.py` (EventType)
- `src/universal_agent/feature_flags.py` (new)
- `src/universal_agent/gateway_server.py` (feature flag placeholders)
- `src/universal_agent/main.py` (feature flag placeholders)
- `src/mcp_server.py` (feature flag placeholders)
- `src/universal_agent/agent_setup.py` (feature flag‑aware memory enablement)
- `Project_Documentation/015_Testing_Strategy.md` (parity workflow; informational)

---

## 7. Handoff instructions (for next agent)
1. Read `/heartbeat/10_Implementation_Plan.md` and follow Phase order.
2. Keep parity tenet intact.
3. Use `/heartbeat/15_Lessons_Learned_and_Plan_Changes.md` for deviations/issues.
4. Implement Phase 1 broadcast before adding any heartbeat scheduling logic.
5. Keep heartbeat + memory **off by default** until stable.

