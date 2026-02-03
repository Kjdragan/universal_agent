# Build‑Out Plan — Clawdbot Feature Parity (Living Plan)

**Owner:** kjdragan + Codex
**Last updated:** 2026‑02‑03
**Scope:** Post‑MVP feature build‑out. This plan is the single source of truth for what is in scope, what’s done, and what’s next.

---

## 0) Working Assumptions
- We do **not** want to clone Clawdbot wholesale; we want to **integrate** it into UA’s architecture.
- We are **not opposed** to taking functionality **verbatim** from Clawdbot when it is the fastest, safest path — as long as it is wired cleanly into UA (config, runtime, observability, and session model).
- Cross‑surface consistency (CLI/Web UI/Telegram) matters more than adding new surfaces quickly.
- We prioritize durability + observability + predictable behavior over “fast hacks.”

---

## 1) Roadmap Snapshot (Phases)

### Phase 1 — **File‑based Memory + Pre‑compaction Flush** (Current Focus)
**Goal:** durable, inspectable memory that survives restarts and improves continuity across CLI/Web UI/Telegram.

### Phase 2 — **Heartbeat / Proactive Loop**
**Goal:** scheduled or proactive runs (digests, checks) with guardrails.

### Phase 3 — **Gateway Ops / Control Plane (Minimal UI)**
**Goal:** operational visibility & control: session status, channel status, skill policy, log tail.

### Phase 4 — **Telegram Parity Improvements**
**Goal:** reliability + formatting parity + observability for Telegram; adopt a few proven patterns (not the whole stack).

---

## 2) Phase 1 — Memory System Build‑Out (Detailed Plan)

### 2.1 Objectives
- Persist **durable memory** to disk (human‑readable files).
- Add a **simple index** for retrieval (FTS/SQLite or JSON index).
- Add a **pre‑compaction flush hook** so memory isn’t lost during context compaction.
- Enable consistent memory behavior across CLI + Gateway + Telegram.

### 2.2 Non‑Goals (for Phase 1)
- No “autonomous scheduling” (that’s Phase 2).
- No new channel integrations.
- No UI work beyond minimal logs or config (that’s Phase 3).

### 2.3 Proposed Design (UA‑native, not Clawdbot‑copy)

**Memory Files**
- `AGENT_RUN_WORKSPACES/<session_id>/MEMORY.md` (primary running memory)
- `AGENT_RUN_WORKSPACES/<session_id>/memory/YYYY‑MM‑DD.md` (daily append)

**Index**
- Lightweight index per session (SQLite FTS or JSON with timestamps + tags).\
- Index updates only when memory is appended/updated.

**Memory Flush Hook**
- Hook before compaction or session close:\
  - Summarize last N turns to memory.\
  - Append to daily memory file.\
  - Update `MEMORY.md` (curated/structured).\

**Retrieval**
- Simple “retrieve memory” step in prompt assembly when available.\
- Use “recency + tag” first, then semantic/FTS search when required.

### 2.4 Implementation Steps (Phase 1)

#### A) Memory IO + Data Model
- [x] Create `memory/` module with:
  - `memory_store.py` (append, update, read)
  - `memory_index.py` (index build/update, search)
  - `memory_models.py` (structured entry schema)
- [x] Add memory path conventions in one place (no magic paths spread across code).

#### B) Memory Injection + Retrieval
- [x] Add retrieval step in prompt build path using current session context (file memory + core memory).
- [x] Provide guardrails to prevent over‑injection (token cap via env).

#### C) Pre‑compaction Hook
- [x] Identify compaction entry point in UA (PreCompact hook).
- [x] Add pre‑compaction callback that writes memory snapshot to files + index.
- [x] Ensure hook is no‑op when memory disabled.

#### D) Config / Feature Flags
- [x] `UA_MEMORY_ENABLED=1` (default on for dev, optional for prod)
- [x] `UA_MEMORY_MAX_TOKENS` (cap memory injection)
- [x] `UA_MEMORY_INDEX=fts|json|off`

#### E) Tests / Validation
- [x] Unit tests for memory store + index update.
- [x] Workspace environment tests updated for async tool APIs.
- [x] Integration test: CLI run → memory flush → retrieval in next run.

### 2.5 Success Criteria (Phase 1)
- Memory persists across restarts.
- Memory is injected on next session turn (verify via logs or debug output).
- Pre‑compaction flush is triggered reliably.
- Memory subsystem does not degrade normal CLI/Web UI behavior.

### 2.6 Risks / Mitigations
- **Risk:** memory injection overwhelms prompt.\
  **Mitigation:** strict token caps + truncation strategy.
- **Risk:** index complexity increases scope.\
  **Mitigation:** ship JSON index first; FTS as follow‑up.

---

## 3) Phase 2 — Heartbeat / Proactive Loop (Clawdbot‑Aligned Plan)

### Objective
Enable scheduled or proactive tasks with guardrails and minimal UX, aligned with Clawdbot’s heartbeat + cron model but integrated into UA’s gateway/runtime.

### 3.1 Clawdbot Reference (What It Actually Does)
Reference implementation:
- Heartbeat config: `src/config/types.agent-defaults.ts` (agents.defaults.heartbeat)
- Visibility defaults: `src/config/types.channels.ts`
- Heartbeat prompt + ACK logic: `src/auto-reply/heartbeat.ts`
- Runner + delivery: `src/infra/heartbeat-runner.ts`
- Wake coalescing + retries: `src/infra/heartbeat-wake.ts`
- Events + last heartbeat: `src/infra/heartbeat-events.ts`
- System events queue: `src/infra/system-events.ts`
- Gateway methods: `src/gateway/server-methods/system.ts` (`last-heartbeat`, `set-heartbeats`, `system-event`, `system-presence`)
- Cron service + gateway wiring: `src/gateway/server-cron.ts`, `src/gateway/server-methods/cron.ts`, `src/cron/service.ts`

Key behavior to match:
- Heartbeat interval (`every`, default 30m).
- Active‑hours window per agent (timezone-aware).
- Target routing: `last`, `none`, or explicit channel + destination.
- Optional heartbeat prompt override and ack token (`HEARTBEAT_OK`) stripping.
- Heartbeat delivery visibility (showOk/showAlerts/useIndicator).
- “Wake” hooks to trigger heartbeat now or next heartbeat.
- Cron job CRUD + run history, with isolated runs and a “cron” command lane.

### 3.2 UA Integration Design
We keep UA’s gateway and event model intact, but mirror Clawdbot semantics:

**Config surface (UA)**
- Add UA‑side config schema for heartbeat parity:
  - `UA_HEARTBEAT_EVERY`, `UA_HEARTBEAT_ACTIVE_HOURS`, `UA_HEARTBEAT_TIMEZONE`
  - `UA_HEARTBEAT_TARGET`, `UA_HEARTBEAT_TO`
  - `UA_HEARTBEAT_PROMPT`, `UA_HEARTBEAT_ACK_MAX_CHARS`
  - `UA_HEARTBEAT_INCLUDE_REASONING`
  - Per‑channel visibility defaults: `UA_HB_SHOW_OK`, `UA_HB_SHOW_ALERTS`, `UA_HB_USE_INDICATOR`
- Honor `HEARTBEAT.md` in workspace, but do not hard‑require it.

**Heartbeat runner (UA)**
- Add heartbeat loop in gateway process only (single runner).
- Use queue/busy gating to skip heartbeats when main lane is busy.
- Implement ACK stripping and “empty heartbeat” skip (Clawdbot’s `HEARTBEAT_OK` + `ackMaxChars`).
- Emit events and last heartbeat cache for UI/API (`/api/v1/heartbeat/last` or WS event).

**Cron (UA)**
- Introduce lightweight cron store + run log (json + jsonl) to mirror Clawdbot:
  - CRUD: list/add/update/remove/run/status/runs
  - Run in isolated agent session with `cron:<jobId>` session key and dedicated lane
  - Persist run log entries

**System events + presence (UA)**
- Add a short in‑memory system event queue (session‑scoped) to include in next prompt.
- Add “presence” updates from gateway (node metadata + reason), broadcast to WS.

### 3.3 Implementation Steps (Detailed)

#### A) Config + Validation
- [x] Add heartbeat config schema and env vars to `.env.sample` + docs.
- [ ] Support per‑agent overrides (defaults + per‑agent).
- [ ] Validate heartbeat target: `last`, `none`, or known channel id.

#### B) Heartbeat Prompt + ACK Logic
- [x] Implement `HEARTBEAT_OK` stripping and max‑chars ACK suppression.
- [x] Use Clawdbot prompt text as default; allow override.
- [x] Skip heartbeat when `HEARTBEAT.md` is effectively empty.

#### C) Heartbeat Runner + Delivery
- [x] Single runner in gateway process.
- [x] Busy‑lane gating + backoff on errors.
- [x] Resolve delivery target (last channel, explicit channel, none).
- [x] Emit heartbeat event payloads + indicator type.
- [ ] Surface last‑heartbeat via gateway API.

#### D) Wake Hooks
- [ ] Implement “wake now” and “wake next heartbeat” API.
- [x] Internal wake signal plumbing (gateway‑local).
- [ ] Allow cron + system events to trigger a heartbeat wake.

#### E) Cron Service (Proactive Loop)
- [ ] Add cron storage and service with CRUD + run + history.
- [ ] Run jobs in isolated session with `cron` lane (no overlap with main lane).
- [ ] Emit cron events to WS + append run logs.

#### F) Tests
- [x] Heartbeat: ACK strip + visibility modes (existing gateway heartbeat tests).
- [ ] Heartbeat: active‑hours window + empty file skip.
- [ ] Wake: now vs next heartbeat coalescing.
- [ ] Cron: CRUD, auto‑run due jobs, run log entries.

### 3.4 Deliverables
- Heartbeat runner parity with Clawdbot semantics.
- Cron service with stable API + run history.
- Heartbeat events visible via WS + API.

### 3.5 Status
- **Current:** MVP heartbeat exists, but not full Clawdbot parity. Cron + system events/presence are missing.
- **Target:** Full parity for heartbeat + cron behavior.

---

## 4) Phase 3 — Gateway Ops / Control Plane (Clawdbot‑Aligned Plan)

### Objective
Expose operational control + visibility in a minimal, reliable way, aligned with Clawdbot’s gateway methods but integrated into UA’s API + UI stack.

### 4.1 Clawdbot Reference (Gateway Methods + Control UI)
Clawdbot’s gateway exposes a wide control plane via WS RPC:
- `sessions.*`, `channels.*`, `skills.*`, `logs.tail`, `config.*`, `models.*`, `usage.*`, `exec.approvals.*`, `cron.*`, `system-*`, `node.*`, `device.*`, `voicewake.*`, `talk.*`, `wizard.*`.
- `control-ui` static UI served by gateway (`src/gateway/control-ui.ts`).

We don’t need all of it, but the structure and boundaries are useful.

### 4.2 UA Integration Design
We define a minimal “Ops API” set and keep it stable:

**Required Ops Methods (Phase 3)**
- Sessions:
  - list / status / preview / reset / delete / compact
- Channels:
  - status (including probe), logout
- Skills:
  - status, enable/disable, env overrides
- Logs:
  - tail with cursor + file selection
- Config:
  - get / set / patch / schema (read‑only in prod by default)

**Nice‑to‑Have (Phase 3.5)**
- Exec approvals status + request/resolve
- Models list + default selection
- System presence + last heartbeat
- Node + device pairing hooks (if we ever distribute runtimes)

**UI (Ops Panel)**
- Minimal “Ops” panel in Web UI:
  - Sessions list + last activity
  - Channel status table
  - Logs tail window
  - Heartbeat status

### 4.3 Implementation Steps (Detailed)

#### A) Gateway Ops API
- [ ] Add REST/WS routes in UA gateway for:
  - `sessions.list`, `sessions.preview`, `sessions.reset`, `sessions.delete`, `sessions.compact`
  - `channels.status`, `channels.logout`
  - `skills.status`, `skills.update`
  - `logs.tail`
  - `config.get`, `config.schema`
- [ ] Add consistent response envelope + error codes.

#### B) Logs Tail (Clawdbot pattern)
- [ ] Implement cursor‑based tail with max bytes and rolling log file resolution.
- [ ] Hard caps to prevent large reads.

#### C) Channels Status + Probes
- [ ] Build channel probes that can run on demand with timeout.
- [ ] Return last inbound/outbound activity and account status.

#### D) Skills Ops
- [ ] Report workspace skill status.
- [ ] Support enable/disable + env injection (without restarting gateway).

#### E) Config Schema
- [ ] Expose config schema for UI.
- [ ] Enforce read‑only mode in production unless explicitly allowed.

#### F) UI Panel
- [ ] Add a small Ops tab in Web UI that consumes above endpoints.
- [ ] Show heartbeat state + last heartbeat event.

### 4.4 Status
- **Current:** minimal status endpoints exist; no unified ops surface.
- **Target:** stable Ops API + basic UI parity with Clawdbot control‑ui features.

---

## 5) Phase 4 — Telegram Parity Improvements (Outline)

### Objective
Increase Telegram reliability and formatting parity.

### Outline Steps
- Add robust HTML formatting fallback + retries.
- Improve chunking and message segmentation.
- Add Telegram‑specific diagnostics flags.
- Adopt partial patterns from Clawdbot (not full stack).

---

## 6) Current Status
**Phase 1:** In progress (file memory + pre‑compaction flush implemented; async tool tests aligned; CLI memory integration test passing).\
**Phase 2:** MVP done, Clawdbot parity pending (cron + full heartbeat config/targets/visibility + system presence).\
**Phase 3:** MVP done, Clawdbot parity pending (ops endpoints breadth + UI panel).\
**Phase 4:** Paused (Telegram reliability work gated; tests disabled by default).

---

## 7) Next Actions (Immediate)
1. Expand Phase 2 to full Clawdbot parity (heartbeat + cron + system events).
2. Expand Phase 3 Ops API + minimal UI panel.
3. Add memory search tests and update the testing guide.

---

## 8) Changelog
- 2026‑02‑03: Created implementation plan; Phase 1 (Memory) prioritized.
- 2026‑02‑03: Updated tests to align with async tools; added test gating for Telegram/heartbeat and Composio uploads.
- 2026‑02‑03: Completed CLI memory integration test (flush → retrieve) and marked Phase 1 validation step done.
- 2026‑02‑03: Added `.env.sample` template with memory defaults (dev on, prod optional).
- 2026‑02‑03: Validated gateway memory injection with a seeded workspace (file memory retrieved via gateway session).
- 2026‑02‑03: Test suite signal stabilized (warnings resolved; clean pytest run).
- 2026‑02‑03: Heartbeat MVP gating wired (enable flag + busy skip + empty‑skip) and heartbeat envs added to `.env.sample`.
- 2026‑02‑03: Heartbeat MVP summary test added and passing (gateway WS broadcast).
- 2026‑02‑03: Phase 3 delivery policy complete (delivery routing + visibility + dedupe + indicator); policy tests added.
