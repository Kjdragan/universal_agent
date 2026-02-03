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

## 3) Phase 2 — Heartbeat / Proactive Loop (Outline)

### Objective
Enable scheduled or proactive tasks with guardrails and minimal UX.

### Outline Steps
- Define heartbeat config format (`HEARTBEAT.md` or env‑driven).
- Implement scheduler loop in gateway (single‑process, limited concurrency).
- Add policy gating (time windows, rate caps, allowlist).
- Emit clear logs/notifications.

---

## 4) Phase 3 — Gateway Ops / Control Plane (Outline)

### Objective
Expose basic operational control + logs for multi‑surface reliability.

### Outline Steps
- Gateway endpoints for:
  - sessions list/status
  - channels list/status
  - skill enable/disable flags
  - log tail
- Minimal “Ops UI” panel in web UI

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
**Phase 2:** Complete (heartbeat summary MVP + busy‑skip + empty‑skip + WS test).\
**Phase 3:** Complete (delivery routing + visibility policy + dedupe + indicator + tests).\
**Phase 4:** Paused (Telegram reliability work gated; tests disabled by default).

---

## 7) Next Actions (Immediate)
1. Phase 4 kickoff (Memory Phase 2): vector index MVP (async build + search).
2. Add memory search tests and update the testing guide.

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
