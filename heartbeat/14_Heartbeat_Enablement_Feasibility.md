---
title: "Heartbeat Enablement (On/Off) Feasibility"
status: draft
last_updated: 2026-01-29
---

# 14. Heartbeat Enablement (On/Off) Feasibility

## 1. Summary
**Yes — it is feasible** to implement the heartbeat system as a **runtime‑toggleable parameter** in Universal Agent. The current architecture already supports:
- A clear lifecycle entry point (gateway `lifespan()`),
- A session‑scoped execution engine, and
- Centralized event streaming.

The key requirement is to **gate the scheduler + wake logic** behind a config flag, and ensure **clean shutdown** when disabled.

## 2. Evidence from existing guidance
### 2.1 Proposed schema already includes an `enabled` flag
The existing heartbeat schema document proposes:
```yaml
heartbeat:
  enabled: true
  every: "30m"
```
See: @/home/kjdragan/lrepos/universal_agent/heartbeat/03_UA_Heartbeat_Schema_and_Event_Model.md#17-38

This supports the on/off control at the configuration layer.

### 2.2 Gateway lifecycle has a single integration point
The prototype design anchors heartbeat scheduling inside `gateway_server.py`’s `lifespan()` context manager (service start/stop).
See: @/home/kjdragan/lrepos/universal_agent/heartbeat/04_Gateway_Scheduler_Prototype_Design.md#6-15

This makes it straightforward to:
- **Start** the scheduler only when `enabled = true`, and
- **Skip creating it** otherwise.

## 3. Feasibility analysis
### 3.1 Toggle scope options
1. **Global flag (simplest):**
   - `heartbeat.enabled = false` disables all heartbeat scheduling.
2. **Per-session override:**
   - `sessions.<id>.heartbeat.enabled = false` disables for only that workspace.
3. **Runtime override:**
   - Allow enabling/disabling at runtime via a control API or CLI.

All three are feasible; (1) + (2) are minimal and align with the proposed schema.

### 3.2 Required implementation hooks
To support a clean on/off toggle:
- **Scheduler creation** must be conditional.
- **Wake requests** should check `enabled` before enqueuing.
- **Shutdown** should cancel the scheduler task gracefully.

### 3.3 Interaction with existing UA constraints
- UA currently streams events only during `execute` cycles, so the enablement flag also needs to **control broadcast** if server‑push is added.
- Heartbeat off = **no server‑side scheduled execution**, so the gateway can remain request‑driven only.

## 4. Recommended enablement design
### 4.1 Config shape
```yaml
heartbeat:
  enabled: true
  every: "30m"
```
Per‑session override:
```yaml
sessions:
  session_abc:
    heartbeat:
      enabled: false
```

### 4.2 Behavior contract
- When `enabled = false`:
  - No scheduler task is created.
  - No heartbeats are queued or executed.
  - No heartbeat events are emitted.

### 4.3 Default
Default to **disabled** until the scheduler is stable and server‑push is in place.

## 5. Risks + mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Toggle changes during runtime | Scheduler may keep running | Add `reload` or `stop()` logic if config hot‑reload is added |
| Missing disable path | Silent scheduling | Enforce `enabled` check in scheduler + wake requests |
| Unclear source of truth | Inconsistent on/off | Single config layer with schema validation |

## 6. Latency + overhead tradeoff (why the toggle matters)
- **Heartbeat on** introduces background scheduling work and occasional agent turns.
- **Heartbeat off** keeps the gateway request‑driven only, eliminating any scheduler overhead.

Given uncertainty about latency and effectiveness, the recommended posture is:
- **Default off** in production until measured benefits outweigh cost.
- **Targeted enablement** for specific sessions or environments.

## 7. Conclusion
Implementing an **on/off heartbeat parameter** is straightforward and consistent with the proposed architecture. The main work is ensuring the scheduler and wake logic are **completely gated** by the flag and providing **clean stop behavior** during shutdown or config changes.

