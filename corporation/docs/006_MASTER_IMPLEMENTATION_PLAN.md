# Corporation Master Implementation Plan

**Created:** 2026-03-01
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

### What Is NOT Yet Done (Gaps)

| Gap ID | Description | Phase |
|---|---|---|
| **G1** | **Generalized Stream Consumer** — Only the tutorial bootstrap worker uses Redis transport. No generalized "mission consumer" loop exists in the UA core that can pull arbitrary delegation missions and route them to the appropriate local VP agent. | Phase 3 |
| **G2** | **Local Factory Deployment Automation** — No script/playbook exists to stand up a LOCAL_WORKER factory on the desktop machine (install, configure `.env` via Infisical, register with HQ, start consumer loop). | Phase 3 |
| **G3** | **Factory Heartbeat Protocol** — Factories register once but there is no periodic heartbeat loop keeping `last_seen_at` fresh. The Corporation View shows stale detection but nothing sends heartbeats. | Phase 3 |
| **G4** | **Cost Analytics** — ZAI API telemetry aggregation across factories is not implemented. | Phase 4 |
| **G5** | **Memo Promotion Pipeline** — No mechanism for local factories to author executive summary memos and promote insights to HQ global knowledge base. | Phase 5 |
| **G6** | **Database Federation** — No explicit Global vs Local state boundary enforcement beyond the conceptual design. Local factories use their own `vp_state.db`. | Phase 5 |
| **G7** | **Factory Template / Redeployment Automation** — No "factory template" package that can be parameterized and deployed to new nodes. No mechanism to push factory updates from repo to all deployed factories. | Phase 3-4 |
| **G8** | **CSI-to-HQ Integration** — CSI is designed as an upstream supplier to HQ, but the actual "push trend reports to Simone" bridge is not wired through the delegation bus. | Phase 4 |

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

**Key Invariant:** Both nodes run identical codebase. Behavior is determined entirely by `.env` parameterization.

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
Redis infrastructure is deployed. The bus and schema exist. What remains is generalizing beyond the tutorial worker.

#### Phase 3a: Generalized Mission Consumer (Next Up)
- [ ] **3a.1** Create `src/universal_agent/delegation/consumer.py` — a generic mission consumer loop that:
  - Polls `ua:missions:delegation` via `RedisMissionBus.consume()`
  - Deserializes `MissionEnvelope` and routes to the appropriate handler based on `payload.task` type
  - Publishes `MissionResultEnvelope` back to `ua:missions:delegation:results`
  - Handles retries, DLQ escalation, and graceful shutdown
  - Runs as a standalone entry point (`python -m universal_agent.delegation.consumer`) or as an async background task within the gateway
- [ ] **3a.2** Define mission task-type registry (e.g., `bootstrap_repo`, `coding_task`, `research_task`, `general_task`) with handler dispatch
- [ ] **3a.3** Wire the tutorial bootstrap worker to use the generalized consumer (replace bespoke polling)
- [ ] **3a.4** Add unit + integration tests for consumer loop, handler dispatch, DLQ escalation
- [ ] **3a.5** Validate end-to-end: HQ publishes mission → local consumer picks up → executes → result published back

#### Phase 3b: Factory Heartbeat Protocol
- [ ] **3b.1** Add periodic heartbeat sender in the consumer/factory process that POSTs to `POST /api/v1/factory/registrations` every 60s with current capabilities and status
- [ ] **3b.2** HQ gateway marks registrations as `stale` when `last_seen_at > 5 minutes`
- [ ] **3b.3** Corporation View UI reflects live heartbeat status (already has stale detection UI)
- [ ] **3b.4** Add tests for heartbeat registration refresh and stale detection

#### Phase 3c: Local Factory Deployment Playbook
- [ ] **3c.1** Create `scripts/deploy_local_factory.sh` — clones repo, installs deps via `uv`, creates `.env` from Infisical `LOCAL_WORKER` profile, starts consumer service
- [ ] **3c.2** Create `deployment/systemd-user/universal-agent-local-factory.service` — systemd unit for the local factory consumer loop
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

### HQ (VPS) `.env`
```env
FACTORY_ROLE=HEADQUARTERS
UA_DEPLOYMENT_PROFILE=vps
ENABLE_VP_CODER=true
UA_DELEGATION_REDIS_ENABLED=1
UA_REDIS_HOST=<vps-host>
UA_REDIS_PORT=6379
UA_REDIS_DB=0
REDIS_PASSWORD=<from-infisical>
UA_DELEGATION_STREAM_NAME=ua:missions:delegation
UA_DELEGATION_CONSUMER_GROUP=ua_workers
UA_DELEGATION_DLQ_STREAM=ua:missions:dlq
```

### Local Factory (Desktop) `.env`
```env
FACTORY_ROLE=LOCAL_WORKER
UA_DEPLOYMENT_PROFILE=local_workstation
ENABLE_VP_CODER=true
LLM_PROVIDER_OVERRIDE=  # optional: ZAI, ANTHROPIC, OPENAI, OLLAMA
UA_DELEGATION_REDIS_ENABLED=1
UA_REDIS_HOST=<vps-host-or-tailnet>
UA_REDIS_PORT=6379
UA_REDIS_DB=0
REDIS_PASSWORD=<from-infisical>
UA_DELEGATION_STREAM_NAME=ua:missions:delegation
UA_DELEGATION_CONSUMER_GROUP=ua_workers
UA_DELEGATION_DLQ_STREAM=ua:missions:dlq
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

1. **Session N+1:** Implement Phase 3a (Generalized Mission Consumer) — this is the critical path that unlocks everything else.
2. **Session N+2:** Implement Phase 3b (Factory Heartbeat) + Phase 3c (Local Factory Deployment).
3. **Session N+3:** Validate end-to-end: HQ → Redis → Local Factory → VP Coder → Result back to HQ.
4. **Session N+4:** Phase 3d (Factory Template & Self-Update) + Phase 4a (Enhanced Corporation View).

Factory improvements (Track B) can happen in any session — they are independent and deploy via normal `git pull` on each factory.

---

## 7. Validation Gates

Each phase has explicit acceptance criteria before moving to the next:

### Phase 3a Gate
- [ ] `uv run pytest tests/delegation/test_consumer.py -q` passes
- [ ] End-to-end: `POST /api/v1/delegation/publish` → consumer picks up → result on results stream

### Phase 3b Gate
- [ ] Factory heartbeat appears in Corporation View with < 2 minute freshness
- [ ] Stale factories correctly flagged after 5 minutes of silence

### Phase 3c Gate
- [ ] Desktop factory starts, registers, and appears in Corporation View
- [ ] Desktop factory receives and completes a test delegation mission

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
| `docs/phases/phase_3a_generalized_consumer.md` | 3a: Generalized Mission Consumer | Not Started |
| `docs/phases/phase_3b_factory_heartbeat.md` | 3b: Factory Heartbeat Protocol | Not Started |
| `docs/phases/phase_3c_local_factory_deployment.md` | 3c: Local Factory Deployment | Not Started |
| `docs/phases/phase_3d_factory_template.md` | 3d: Factory Template & Self-Update | Not Started |
| `docs/phases/phase_4_observability.md` | 4: Observability & CSI Bridge | Not Started |
| `docs/phases/phase_5_memory_federation.md` | 5: Memory & Federation | Not Started |

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
