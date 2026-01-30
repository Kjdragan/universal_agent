# 10. Phased Implementation Plan (Heartbeat + Memory + Telegram + Deployment)

This plan is intentionally cautious to protect the stability of the current system. It integrates the new findings from:
- Heartbeat design + scheduler: @/home/kjdragan/lrepos/universal_agent/heartbeat/03_UA_Heartbeat_Schema_and_Event_Model.md#1-134
- Broadcast vs Option C: @/home/kjdragan/lrepos/universal_agent/heartbeat/06_WebSocket_Broadcast_and_Option_C.md#1-78
- Clawdbot heartbeat analysis: @/home/kjdragan/lrepos/universal_agent/heartbeat/05_Clawdbot_Heartbeat_System_Report.md#1-84
- Memory feasibility + migration: @/home/kjdragan/lrepos/universal_agent/heartbeat/12_UA_Memory_Feasibility_and_Implementation.md#1-116
- Heartbeat enablement toggle: @/home/kjdragan/lrepos/universal_agent/heartbeat/14_Heartbeat_Enablement_Feasibility.md#1-99

## Guiding guardrails (non‑negotiable)
- **Default off** for heartbeat + memory indexing in production.
- No change to existing CLI direct behavior unless explicitly enabled.
- **UI‑agnostic parity:** CLI direct, CLI via gateway, Web UI, and Telegram must use the same execution path and produce equivalent results (UI differences only).
- Every phase includes **entry/exit gates** and a **rollback** path.
- New services must be **kill‑switchable** via config.

---

## Phase 0 — Safety baseline + config scaffolding (no behavior change)
**Goal:** finalize contracts and add feature flags without changing runtime behavior.

**Work:**
1. Confirm heartbeat prompt + suppression contract (`UA_HEARTBEAT_OK`).
2. Decide on `EventType.HEARTBEAT` vs `STATUS.kind="heartbeat"` (D‑002).
3. Implement the chosen heartbeat event type in `agent_core` + WebSocket events.
4. Add config schema placeholders for:
   - `heartbeat.enabled` (default **false**) @/home/kjdragan/lrepos/universal_agent/heartbeat/14_Heartbeat_Enablement_Feasibility.md#58-95
   - `memory.enabled` (default **false**)
5. Add explicit kill switches (env vars) for heartbeat + memory indexing.
6. Define regression checklist (CLI, gateway WS, Telegram bot startup).
7. Expand testing docs with a Phase 0 smoke checklist (update the parity suite + UI runbook). @/home/kjdragan/lrepos/universal_agent/Project_Documentation/015_Testing_Strategy.md#37-67
8. Document the UI‑agnostic parity contract and add a parity test matrix to the testing strategy.
9. Add agent-browser steps for Web UI parity verification in the testing strategy.

**Gate (must pass):**
- CLI + gateway smoke tests pass with all toggles **off**.

---

## Phase 1 — Transport foundation (WebSocket broadcast)
**Goal:** enable server push **without** altering execution logic.

**Work:**
1. Extend `ConnectionManager` to map `session_id -> set[WebSocket]`.
2. Add `broadcast(session_id, event)` helper (Option A). @/home/kjdragan/lrepos/universal_agent/heartbeat/06_WebSocket_Broadcast_and_Option_C.md#14-31
3. Emit a test “server notice” event on connect (no heartbeat yet).
4. Keep `execute` request flow unchanged.
5. Add gateway WS broadcast tests to `tests/gateway` and record commands in the testing strategy doc. @/home/kjdragan/lrepos/universal_agent/Project_Documentation/015_Testing_Strategy.md#1-53

**Gate:**
- Broadcast test with 2 clients on same session.
- Existing WS streaming still works.

---

## Phase 2 — Heartbeat MVP (scheduler + summary only)
**Goal:** run heartbeat safely with **summary events only**, no external delivery.

**Work:**
1. Add `HeartbeatService` in gateway `lifespan()` with `enabled` check. @/home/kjdragan/lrepos/universal_agent/heartbeat/04_Gateway_Scheduler_Prototype_Design.md#6-33
2. Implement **busy gating** (skip if session executing) + retry.
3. Read `HEARTBEAT.md`; skip if empty to save tokens.
4. Implement `UA_HEARTBEAT_OK` suppression.
5. Emit one **heartbeat summary** event via broadcast.
6. Persist minimal `heartbeat_state.json` for dedupe + last run metadata.
7. Add stabilization tests for heartbeat summary events (gateway path) and update the test strategy doc.

**Gate:**
- Heartbeat runs only when enabled.
- No interference with normal `execute` runs.
- Summary event arrives on WS.

---

## Phase 3 — Heartbeat delivery + visibility policy
**Goal:** deliver heartbeat outputs to explicit targets without spam.

**Work:**
1. Add delivery routing: `last | explicit | none`.
2. Add visibility policy: `showOk`, `showAlerts`, `useIndicator`.
3. Implement dedupe window (24h) and last‑message hash.
4. Add optional “indicator‑only” events for OK cases.
5. Add integration tests for delivery routing + dedupe; document expected results in the testing guide.

**Gate:**
- No duplicate alerts inside dedupe window.
- Delivery policy behaves as configured.

---

## Phase 4 — Memory Phase 1 (file‑based + safe reads)
**Goal:** introduce **auditable memory files** without embeddings.

**Work:**
1. Scaffold `MEMORY.md` + `memory/YYYY-MM-DD.md` in each workspace.
2. Add `ua_memory_get` tool with strict path guards.
3. Make memory writing explicit; no auto‑indexing yet.
4. Add unit tests for path guards + memory file scaffolding; note new commands in the testing strategy doc.

**Gate:**
- `ua_memory_get` can only read memory files.
- No runtime changes when memory is disabled.

---

## Phase 5 — Memory Phase 2 (vector index MVP)
**Goal:** add semantic search without blocking runs.

**Work:**
1. Implement SQLite schema (`files`, `chunks`, `embedding_cache`).
2. Choose provider (OpenAI/Gemini) + batch embeddings.
3. Add `ua_memory_search` with vector similarity.
4. Build index **async** and cache embeddings by hash.
5. Add unit/integration tests for indexing + query scoring; update testing strategy to include a memory test subset.

**Gate:**
- Index builds asynchronously (no request latency regressions).
- Search returns expected results on sample corpus.

---

## Phase 6 — Memory Phase 3 (hybrid + watchers + transcripts)
**Goal:** improve recall + freshness, still opt‑in.

**Work:**
1. Add FTS5 + hybrid scoring if supported.
2. Add watcher + interval sync for memory files.
3. Add optional transcript indexing with retention limits.
4. Add tests for watcher debounce + transcript opt‑in; update the test strategy doc with new runbook steps.

**Gate:**
- Performance within target (e.g., <200ms on medium corpus).
- Clear opt‑in controls for transcript indexing.

---

## Phase 7 — Telegram revival + gateway alignment
**Goal:** restore Telegram with gateway parity (no forked engine).

**Work (two steps):**
1. **Quick revival (optional):** validate webhook + env vars; keep existing adapter if needed for smoke tests.
2. **Gateway alignment:** replace `AgentAdapter` with gateway client; map `telegram_user_id → gateway_session_id`.
3. Add heartbeat alert delivery (alerts only by default).
4. Add manual Telegram regression checklist to the UI testing guide and record gateway/Telegram parity tests.

**Gate:**
- Telegram `/agent` returns same output as web UI.
- Heartbeat alerts can reach Telegram when explicitly enabled.

---

## Phase 8 — Deployment hardening + multi‑user ops
**Goal:** stable remote runtime with safe multi‑user behavior.

**Work:**
1. Choose Railway topology (R2 preferred). @/home/kjdragan/lrepos/universal_agent/heartbeat/09_Deployment_Strategy_and_Railway.md#30-71
2. Ensure persistent storage for `runtime.db` + `AGENT_RUN_WORKSPACES/`.
3. Add per‑user session mapping + allowlist enforcement.
4. Add health checks + uptime monitoring; run overnight soak test.
5. Expand the stabilization suite to include remote gateway + Telegram + heartbeat smoke checks.

**Gate:**
- 6–12 hour soak test without dropped WS or lost heartbeats.
- No regressions in CLI direct mode.

---

## Summary of risk containment
- Heartbeat + memory indexing are **feature‑flagged** and **off by default**.
- Broadcast is introduced **before** any scheduled execution.
- Memory moves from file‑only → vector → hybrid **in separate gates**.
- Telegram is aligned with gateway only after heartbeat is stable.
- Deployment changes are validated last with soak tests.

---

## Phase readiness checklist (use this as the live tracker)
For each phase, mark items **done** before starting the next phase. This section is the **source of truth** for progress.

### Phase 0 readiness
- [ ] Heartbeat prompt + suppression contract finalized.
- [x] Event type decision documented (new `EventType` or `STATUS.kind`).
- [x] Heartbeat event type implemented in `agent_core` + WebSocket events.
- [x] Feature flags module added (heartbeat + memory).
- [x] Feature flags wired into runtime (no behavior change).
- [ ] Regression checklist defined and executed with toggles off (direct + gateway smoke done; UI pending).
- [x] Parity test matrix documented (CLI direct ↔ gateway ↔ Web UI).
- [x] Agent-browser parity workflow documented.
- [ ] Agent-browser parity workflow executed on Web UI.
- [ ] Baseline parity checks run (CLI direct ↔ gateway ↔ Web UI) — CLI direct + gateway done.

### Phase 1 readiness
- [x] WS broadcast works for multiple connections.
- [x] Execute streaming unchanged.
- [x] Broadcast test or integration check passing.

### Phase 2 readiness
- [ ] Scheduler guarded by `heartbeat.enabled`.
- [ ] Busy gating + retry logic validated.
- [ ] `HEARTBEAT.md` empty‑skip confirmed.
- [ ] Summary event emitted and visible on WS.

### Phase 3 readiness
- [ ] Delivery routing (`last | explicit | none`) implemented.
- [ ] Visibility policy enforced.
- [ ] Deduplication window active.

### Phase 4 readiness (memory file‑only)
- [ ] Memory files scaffolded in workspaces.
- [ ] `ua_memory_get` path‑restricted.
- [ ] No runtime changes when memory disabled.

### Phase 5 readiness (memory vector MVP)
- [ ] SQLite schema + embedding cache in place.
- [ ] Embedding provider configured.
- [ ] `ua_memory_search` returns correct hits.
- [ ] Index builds asynchronously.

### Phase 6 readiness (hybrid + watchers)
- [ ] FTS/hybrid scoring validated.
- [ ] File watchers + interval sync stable.
- [ ] Transcript indexing opt‑in with retention limits.

### Phase 7 readiness (Telegram alignment)
- [ ] Gateway client path works in Telegram.
- [ ] Session mapping per user confirmed.
- [ ] Heartbeat alerts reach Telegram when enabled.

### Phase 8 readiness (deployment + ops)
- [ ] Railway topology chosen + documented.
- [ ] Persistent storage confirmed.
- [ ] Soak test completed (6–12 hours).
