# Corporation Master Implementation Plan

**Created:** 2026-03-01
**Updated:** 2026-03-06 (Track B audit, Infisical-first approach, Phase 3a reframe)
**Purpose:** Single source of truth for resuming and continuing the Corporation build-out.
**Audience:** Any AI coder or human picking up this work.

---

## 1. Situation Assessment (Where We Left Off)

### What Exists Today (Verified in Code)

| Layer | Artifact | Status |
|---|---|---|
| **Factory Role System** | `src/universal_agent/runtime_role.py` — `FactoryRole` enum (`HEADQUARTERS`, `LOCAL_WORKER`, `STANDALONE_NODE`), `FactoryRuntimePolicy` dataclass, `build_factory_runtime_policy()` | **Implemented & tested** |
| **Runtime Bootstrap** | `src/universal_agent/runtime_bootstrap.py` — unified bootstrap wiring secrets + policy + LLM override | **Implemented** |
| **Infisical Secrets** | `src/universal_agent/infisical_loader.py` — centralized secret bootstrap with fail-closed for VPS/standalone | **Implemented & tested** (4 unit tests) |
| **Gateway Role Enforcement** | `gateway_server.py` — HTTP middleware blocks non-allowed routes for `LOCAL_WORKER` (health-only surface), ops-token issuance restricted to HQ, delegation publish/consume gated by policy | **Implemented & tested** (5 auth/role surface tests) |
| **Ops Token Issuance** | `POST /auth/ops-token` — JWT issuance, HQ-only, 1-hour TTL | **Implemented** |
| **Delegation Schema** | `src/universal_agent/delegation/schema.py` — `MissionEnvelope`, `MissionPayload`, `MissionResultEnvelope` (Pydantic) | **Implemented** |
| **Redis Mission Bus** | `src/universal_agent/delegation/redis_bus.py` — `RedisMissionBus` with publish, consume, ack, DLQ, consumer groups | **Implemented & tested** |
| **Redis Infrastructure** | `corporation/infrastructure/redis/` — `docker-compose.yml`, `redis.conf`, deployment README | **Drafted & validated on VPS** |
| **Redis Deploy Script** | `scripts/install_vps_redis_bus.sh` | **Implemented** |
| **Factory Capabilities API** | `GET /api/v1/factory/capabilities` | **Implemented & live on VPS** |
| **Factory Registration API** | `GET/POST /api/v1/factory/registrations` (HQ-only) | **Implemented & live on VPS** |
| **Corporation View UI** | `web-ui/app/dashboard/corporation/page.tsx` — fleet dashboard with role, status, heartbeat, delegation bus metrics, registration table | **Implemented, built, deployed to VPS** |
| **HQ Nav Gating** | Dashboard layout only shows Corporation View when `factory_role == HEADQUARTERS` | **Implemented** |
| **Agent Capability Gating** | `agent_setup.py` — reads `FACTORY_ROLE` and `ENABLE_VP_CODER` to filter `capabilities.md` generation | **Implemented** |
| **Tutorial Worker (Redis transport)** | `scripts/tutorial_local_bootstrap_worker.py --transport redis` end-to-end validated | **Implemented & validated** |
| **CSI Rebuild** | `docs/csi-rebuild/` — Phase 1 reliability in progress (packet 8 next), separate from Corporation work | **Independent workstream** |

#### Track B Discoveries (2026-03-06 Audit)

The following systems were built since the plan creation as part of ongoing HQ improvements. They significantly impact the corporation rollout:

| Layer | Artifact | Impact on Plan |
|---|---|---|
| **VP External Worker System** | `src/universal_agent/vp/` — `VpWorkerLoop`, `dispatcher.py`, `ClaudeCodeClient`, `ClaudeGeneralistClient`, `VpProfile` registry, 15+ feature flags | **Partially supersedes Phase 3a** — complete local mission consumer with SQLite lifecycle (queue/claim/heartbeat/finalize/events). Only needs Redis→SQLite bridge for cross-machine. |
| **VP Mission Lifecycle** | `src/universal_agent/durable/state.py` — `queue_vp_mission`, `claim_next_vp_mission`, `heartbeat_vp_session_lease`, `finalize_vp_mission`, `append_vp_event` | **Provides Phase 3a foundation** — mature mission lifecycle already exists in SQLite. |
| **SessionContext Concurrency** | `Refactor_Workspace/` — 6-phase refactor replacing global execution locks with per-session `ContextVar` isolation | **Enables multi-mission concurrency** — critical for factory handling multiple delegated missions. |
| **GWS MCP Bridge** | `src/universal_agent/services/gws_mcp_bridge.py` — 195 Google Workspace tools via `gws` CLI | HQ-only capability (disabled on LOCAL_WORKER via feature flag). |
| **Process Heartbeat** | `src/universal_agent/process_heartbeat.py` — OS-level liveness file for watchdog daemon thread | **Partially addresses Phase 3b** — OS-level heartbeat exists; needs HQ registration heartbeat. |
| **Threads CSI Channel** | Webhooks, publishing, semantic enrichment | HQ-only capability. |
| **Todoist Integration** | Rich handoff skill, heartbeat injection | HQ-only capability. |
| **Infisical Env Provisioning** | `scripts/infisical_provision_factory_env.py` — automated environment cloning with role overrides | **Replaces Phase 3c `.env.factory.template`** — Infisical is canonical parameter store, not dotenv. |

### What Is NOT Yet Done (Gaps)

| Gap ID | Description | Phase | Status |
|---|---|---|---|
| **G1** | **~~Generalized Stream Consumer~~** → **Redis→SQLite Bridge** — VP worker system provides the local consumer. What's missing is a thin adapter that consumes Redis missions and inserts them into local VP SQLite for `VpWorkerLoop` pickup. | Phase 3a | **Partially mitigated** |
| **G2** | **Local Factory Deployment Automation** — Infisical `kevins-desktop` environment provisioned. Need deploy script and systemd service. | Phase 3c | **In progress** |
| **G3** | **Factory Heartbeat Protocol** — VP worker has local SQLite heartbeats + process_heartbeat.py provides OS liveness. Missing: periodic registration heartbeat to HQ. | Phase 3b | **Partially mitigated** |
| **G4** | **Cost Analytics** — ZAI API telemetry aggregation across factories is not implemented. | Phase 4 | Open |
| **G5** | **Memo Promotion Pipeline** — No mechanism for local factories to author executive summary memos and promote insights to HQ global knowledge base. | Phase 5 | Open |
| **G6** | **Database Federation** — No explicit Global vs Local state boundary enforcement beyond the conceptual design. Local factories use their own `vp_state.db`. | Phase 5 | Open |
| **G7** | **Factory Template / Redeployment Automation** — No "factory template" package that can be parameterized and deployed to new nodes. No mechanism to push factory updates from repo to all deployed factories. | Phase 3-4 | Open |
| **G8** | **CSI-to-HQ Integration** — CSI is designed as an upstream supplier to HQ, but the actual "push trend reports to Simone" bridge is not wired through the delegation bus. | Phase 4 | Open |

---

## 2. Architecture Recap

```
                    ┌─────────────────────────────┐
                    │         CEO (User)           │
                    │   interacts via Telegram/UI  │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
                    │   HEADQUARTERS (VPS)         │
                    │   FACTORY_ROLE=HEADQUARTERS   │
                    │   ┌────────┐ ┌────────┐      │
                    │   │ Simone │ │ Codie  │      │
                    │   │(Primary)│ │(VP Cdr)│      │
                    │   └────────┘ └────────┘      │
                    │   FastAPI Gateway (full)      │
                    │   Next.js UI                  │
                    │   Telegram Polling             │
                    │   Redis Bus (publish+listen)  │
                    │   CSI ← upstream supplier     │
                    │   PostgreSQL (global state)    │
                    └──────────┬──────────────────┘
                               │ Redis Streams
                               │ ua:missions:delegation
                    ┌──────────▼──────────────────┐
                    │   LOCAL FACTORY (Desktop)     │
                    │   FACTORY_ROLE=LOCAL_WORKER   │
                    │   ┌────────┐ ┌────────┐      │
                    │   │ Homer  │ │  Lisa  │      │
                    │   │(Primary)│ │(VP Cdr)│      │
                    │   └────────┘ └────────┘      │
                    │   Health-only API surface     │
                    │   No UI / No Telegram         │
                    │   Redis Bus (listen only)     │
                    │   SQLite (local state)         │
                    └─────────────────────────────┘
```

**Key Invariant:** Both nodes run identical codebase. Behavior is determined entirely by **Infisical environment parameterization** (not `.env` files — see `corporation/docs/INFISICAL_ENVIRONMENTS.md`).

**Architecture Decision (D-006, 2026-03-06):** Cross-machine delegation uses **Option B: Redis→SQLite bridge**. Redis Streams provide the cross-machine transport. A thin bridge adapter on each LOCAL_WORKER consumes Redis missions and inserts them into the local VP SQLite `vp_missions` table. The existing `VpWorkerLoop` then picks them up and executes them. This avoids building a new consumer from scratch — the VP worker system already handles mission lifecycle.

---

## 3. Concurrent Development Model

The plan supports **two parallel tracks** that do not block each other:

- **Track A: Corporation Infrastructure** — building the fleet coordination, delegation, and observability layers.
- **Track B: Factory Improvements** — enhancing the UA core (agents, skills, VP coders, CSI, UI). Changes here are deployed to ALL factories via the same codebase.

**The contract:** Factory improvements land on `main`. Corporation infrastructure also lands on `main`. A factory redeployment pulls `main` and applies its `.env` parameterization. Neither track produces changes that break the other because:
1. All corporation features are gated behind `FACTORY_ROLE` / feature flags.
2. Factory improvements are role-agnostic (they work on any factory).

---

## 4. Phased Implementation Plan

### Phase 1: Evergreen Headquarters ✅ COMPLETE
Single-node HQ running on VPS with Simone, VP Coders, Web UI, Telegram.

### Phase 2: Foundation & Parameterization ✅ COMPLETE
- [x] Infisical centralized secrets with fail-closed
- [x] `FACTORY_ROLE` enum and `FactoryRuntimePolicy`
- [x] Agent capability gating in `agent_setup.py`
- [x] Gateway HTTP surface enforcement (LOCAL_WORKER = health-only)
- [x] Ops token issuance (HQ-only, JWT, 1hr TTL)
- [x] Local worker bridging (tutorial bootstrap worker + systemd service)

### Phase 3: Message Bus & Generalized Delegation 🔶 IN PROGRESS
Redis infrastructure is deployed. The bus and schema exist. The VP external worker system (Track B) provides a complete local mission consumer via SQLite. What remains is bridging Redis (cross-machine) to VP SQLite (local execution).

#### Phase 3a: Redis→SQLite Bridge Adapter (Reframed — Next Up)
> **Note (2026-03-06):** Original scope was "build generalized consumer from scratch." The VP worker system (`src/universal_agent/vp/`) now provides the local consumer. Phase 3a is reframed to building the thin cross-machine bridge only.

- [ ] **3a.1** Create `src/universal_agent/delegation/redis_vp_bridge.py` — a thin bridge that:
  - Polls `ua:missions:delegation` via `RedisMissionBus.consume()`
  - Deserializes `MissionEnvelope` and transforms it to a `queue_vp_mission()` call
  - Inserts into local VP SQLite `vp_missions` table
  - `VpWorkerLoop` (already running) picks up and executes the mission
  - Mission results are published back to `ua:missions:delegation:results` via `RedisMissionBus`
  - Handles ack/DLQ escalation and graceful shutdown
  - Runs as a background task within the LOCAL_WORKER gateway or as a standalone entry point
- [ ] **3a.2** Map Redis `MissionEnvelope.payload.task` types to VP mission types (`coding_task` → CODIE, `general_task` → Generalist, etc.)
- [ ] **3a.3** Add result-back bridge: monitor VP mission finalization → publish `MissionResultEnvelope` to Redis results stream
- [ ] **3a.4** Add unit + integration tests for bridge (Redis consume → SQLite insert → VP pickup → result publish)
- [ ] **3a.5** Validate end-to-end: HQ publishes mission → Redis → bridge inserts into SQLite → VpWorkerLoop executes → result back on Redis

#### Phase 3b: Factory Heartbeat Protocol
- [ ] **3b.1** Add periodic heartbeat sender in the consumer/factory process that POSTs to `POST /api/v1/factory/registrations` every 60s with current capabilities and status
- [ ] **3b.2** HQ gateway marks registrations as `stale` when `last_seen_at > 5 minutes`
- [ ] **3b.3** Corporation View UI reflects live heartbeat status (already has stale detection UI)
- [ ] **3b.4** Add tests for heartbeat registration refresh and stale detection

#### Phase 3c: Local Factory Deployment (Infisical-First)
> **Note (2026-03-06):** Replaces `.env.factory.template` approach with Infisical environment provisioning. See `corporation/docs/INFISICAL_ENVIRONMENTS.md`.

- [x] **3c.0** Provision Infisical `kevins-desktop` environment via `scripts/infisical_provision_factory_env.py`
- [ ] **3c.1** Create `scripts/deploy_local_factory.sh` — clones repo, installs deps via `uv`, creates minimal `.env` with Infisical credentials only, starts factory services
- [ ] **3c.2** Create `deployment/systemd-user/universal-agent-local-factory.service` — systemd unit for local factory (gateway + Redis→SQLite bridge + VP workers)
- [ ] **3c.3** Document the local factory setup in `corporation/docs/LOCAL_FACTORY_SETUP.md`
- [ ] **3c.4** Validate: deploy local factory on desktop → registers with HQ → appears in Corporation View → receives and executes a test delegation

#### Phase 3d: Factory Template & Redeployment
- [ ] **3d.1** Create `scripts/update_factory.sh` — pulls latest `main`, reinstalls deps, restarts consumer service
- [ ] **3d.2** HQ delegation: add a `system:update_factory` mission type that triggers the factory to self-update (pull + restart)
- [ ] **3d.3** Validate: push a change to main → HQ publishes update mission → local factory self-updates and re-registers

### Phase 4: Corporation Observability & Integration 🔴 NOT STARTED

#### Phase 4a: Enhanced Corporation View
- [ ] **4a.1** Add per-factory workload display (active missions, queue depth)
- [ ] **4a.2** Add delegation history table (recent missions published/completed/failed)
- [ ] **4a.3** Add mission detail drill-down (view mission envelope, result, timing)

#### Phase 4b: Cost Analytics
- [ ] **4b.1** Add ZAI API telemetry collection per-factory (track tokens/cost per mission)
- [ ] **4b.2** Aggregate and display in Corporation View

#### Phase 4c: CSI-to-HQ Bridge
- [ ] **4c.1** Wire CSI `opportunity_bundle_ready` events to publish as delegation missions when they require action
- [ ] **4c.2** HQ Simone receives CSI trend reports via the same delegation bus path
- [ ] **4c.3** Validate: CSI emits opportunity → HQ ingests → Simone can act on it

### Phase 5: Organizational Memory & Federation 🔴 NOT STARTED

#### Phase 5a: Memo Promotion Pipeline
- [ ] **5a.1** Define "Executive Summary Memo" schema (structured output from local factory session completion)
- [ ] **5a.2** Local factory auto-generates memo on mission completion
- [ ] **5a.3** HQ ingests and indexes memos into global knowledge base
- [ ] **5a.4** Threshold-based promotion: only universally applicable lessons get promoted

#### Phase 5b: Database Federation
- [ ] **5b.1** Formalize Global State (HQ PostgreSQL) vs Local State (factory SQLite) boundary
- [ ] **5b.2** Local factories query HQ API for global state on boot
- [ ] **5b.3** Add conflict resolution policy for state that drifts

---

## 5. Env Contract Reference

> **Important (2026-03-06):** All parameters are stored in **Infisical**, not in `.env` files. Each machine has its own Infisical environment (e.g., `dev` for VPS HQ, `kevins-desktop` for the desktop). The local `.env` file contains **only** Infisical credentials. See `corporation/docs/INFISICAL_ENVIRONMENTS.md` for the full environment strategy.

### HQ (VPS) — Infisical environment: `dev`
Key parameters (loaded from Infisical at startup):
```
FACTORY_ROLE=HEADQUARTERS
UA_DEPLOYMENT_PROFILE=vps
INFISICAL_ENVIRONMENT=dev
UA_DELEGATION_REDIS_ENABLED=1
UA_VP_EXTERNAL_DISPATCH_ENABLED=1
UA_ENABLE_HEARTBEAT=1
UA_ENABLE_GWS_CLI=1
```

### Local Factory (Desktop) — Infisical environment: `kevins-desktop`
Key parameters (loaded from Infisical at startup):
```
FACTORY_ROLE=LOCAL_WORKER
UA_DEPLOYMENT_PROFILE=local_workstation
INFISICAL_ENVIRONMENT=kevins-desktop
UA_DELEGATION_REDIS_ENABLED=1
UA_VP_EXTERNAL_DISPATCH_ENABLED=0
UA_ENABLE_HEARTBEAT=0
UA_ENABLE_GWS_CLI=0
```

### Minimal local `.env` (only Infisical credentials)
```env
INFISICAL_CLIENT_ID=<machine-identity-from-infisical>
INFISICAL_CLIENT_SECRET=<machine-identity-from-infisical>
INFISICAL_PROJECT_ID=<shared-project-id>
INFISICAL_ENVIRONMENT=kevins-desktop
```

### Role-to-Runtime Behavior Matrix

| Component | `HEADQUARTERS` | `LOCAL_WORKER` | `STANDALONE_NODE` |
|---|---|---|---|
| FastAPI Gateway | Full | Health-only | Full |
| Next.js UI | Yes | No | Yes |
| Telegram Polling | Yes | No | Optional |
| Heartbeat Loop | Global | Local | Local |
| Delegations | Publish & Listen | Listen & Process Only | Disabled |
| VP Coder | If `ENABLE_VP_CODER` | If `ENABLE_VP_CODER` | If `ENABLE_VP_CODER` |

### Redis Stream Naming
- **Stream:** `ua:missions:delegation`
- **Consumer Group:** `ua_workers`
- **DLQ Stream:** `ua:missions:dlq`
- **Results Stream:** `ua:missions:delegation:results`
- **Consumer Names:** `worker_{FACTORY_ID}`

### Mission Envelope Schema
```json
{
  "job_id": "uuid-v4",
  "idempotency_key": "uuid_or_hash",
  "priority": 1,
  "timeout_seconds": 3600,
  "max_retries": 3,
  "payload": {
    "task": "<task_type>:<description>",
    "context": {}
  }
}
```

---

## 6. Immediate Next Steps (Recommended Session Order)

1. **Session N+1:** Implement Phase 3a-bridge (`redis_vp_bridge.py`) — thin Redis consumer → VP SQLite bridge. This is the critical path.
2. **Session N+2:** Implement Phase 3c deploy script + systemd services for desktop factory.
3. **Session N+3:** Validate end-to-end: HQ publishes mission → Redis → desktop bridge → VP SQLite → VpWorkerLoop → result back on Redis.
4. **Session N+4:** Phase 3b (HQ registration heartbeat) + Phase 3d (Factory Template & Self-Update).
5. **Session N+5:** Phase 4a (Enhanced Corporation View) + Phase 4c (CSI-to-HQ Bridge).

Factory improvements (Track B) can happen in any session — they are independent and deploy via normal `git pull` on each factory.

---

## 7. Validation Gates

Each phase has explicit acceptance criteria before moving to the next:

### Phase 3a Gate
- [ ] `uv run pytest tests/delegation/test_redis_vp_bridge.py -q` passes
- [ ] End-to-end: HQ publishes mission → Redis → bridge inserts into VP SQLite → VpWorkerLoop executes → result on Redis results stream

### Phase 3b Gate
- [ ] Factory heartbeat appears in Corporation View with < 2 minute freshness
- [ ] Stale factories correctly flagged after 5 minutes of silence

### Phase 3c Gate
- [ ] Desktop factory starts with Infisical `kevins-desktop` environment
- [ ] Factory registers and appears in Corporation View
- [ ] Desktop factory receives and completes a test delegation mission via Redis→SQLite bridge

### Phase 3d Gate
- [ ] `system:update_factory` mission triggers self-update on local factory
- [ ] Factory re-registers with updated capabilities after update

### Phase 4 Gate
- [ ] Corporation View shows per-factory workload and delegation history
- [ ] CSI opportunities flow through delegation bus to HQ

---

## 8. Risk Register

| Risk | Mitigation |
|---|---|
| Redis exposed to internet | UFW firewall rules + `requirepass` + Tailscale preferred path |
| Split-brain (both factories reply to same user message) | Only HQ has Telegram polling; LOCAL_WORKER cannot initiate external comms |
| Factory credential leak | Infisical machine identities + short-lived ops tokens + least-privilege parameterization |
| Database drift between HQ and local | Explicit Global/Local state boundary; local factories query HQ API for global state |
| Factory update breaks consumer | Graceful shutdown on SIGTERM; consumer acks only after successful execution |
| Redis bus downtime | Gateway falls back to HTTP queue; delegation metrics report connection state |
| Two parallel dispatch systems (Redis + VP SQLite) | Redis→SQLite bridge provides clean separation: Redis for cross-machine, SQLite for local execution |
| Infisical environment drift | Provisioning script is idempotent; re-run to sync from `dev` baseline |

---

## 9. Document Index (Corporation Docs)

### Live Tracking
| Doc | Purpose |
|---|---|
| `status.md` | **LIVE STATUS** — dynamic progress board, validation snapshot, open risks, next step |
| `docs/006_MASTER_IMPLEMENTATION_PLAN.md` | **THIS DOCUMENT** — master plan, architecture, env contracts, validation gates |

### Phase Specifications (Detailed Implementation Guides)
| Doc | Phase | Status |
|---|---|---|
| `docs/phases/phase_3a_generalized_consumer.md` | 3a: Redis→SQLite Bridge (reframed) | Partially Done |
| `docs/phases/phase_3b_factory_heartbeat.md` | 3b: Factory Heartbeat Protocol | Partially Done |
| `docs/phases/phase_3c_local_factory_deployment.md` | 3c: Local Factory Deployment (Infisical-first) | In Progress |
| `docs/phases/phase_3d_factory_template.md` | 3d: Factory Template & Self-Update | Not Started |
| `docs/phases/phase_4_observability.md` | 4: Observability & CSI Bridge | Not Started |
| `docs/phases/phase_5_memory_federation.md` | 5: Memory & Federation | Not Started |

### Infisical & Environment Strategy
| Doc | Purpose |
|---|---|
| `docs/INFISICAL_ENVIRONMENTS.md` | Machine-named environments, override tables, provisioning guide |
| `scripts/infisical_provision_factory_env.py` | Automated Infisical environment cloning with role overrides |

### Design & Architecture
| Doc | Purpose |
|---|---|
| `README.md` | Directory overview |
| `docs/design/001_PRD.md` | Product requirements |
| `docs/design/003_IMPLEMENTATION_PLAN.md` | Original phased plan (superseded by 006 for active tracking) |
| `docs/design/004_CURRENT_EXPLANATION.md` | Handoff Q&A and architecture decisions |
| `docs/002_IMPLEMENTATION_HANDOFF_2026-02-28.md` | Codex session handoff (Infisical + tutorial UX) |
| `docs/004_DISTRIBUTED_FACTORIES_ARCHITECTURE.md` | Symmetrical factory architectural analysis |
| `docs/005_CORPORATION_AND_FACTORIES_ARCHITECTURE.md` | Corporate hierarchy, memory sync, security, rollout strategy |
| `infrastructure/redis/` | Redis bus Docker config, deployment README |

### Track B Reference (HQ Improvements Affecting Plan)
| Artifact | Impact |
|---|---|
| `src/universal_agent/vp/` | VP external worker system — supersedes Phase 3a consumer |
| `src/universal_agent/process_heartbeat.py` | OS-level liveness — partially addresses Phase 3b |
| `src/universal_agent/execution_context.py` | Session workspace ContextVar — enables concurrent missions |
| `Refactor_Workspace/parallel-refactor-progress.md` | SessionContext concurrency refactor log |
