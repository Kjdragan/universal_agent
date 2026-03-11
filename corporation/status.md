# Corporation Build-Out Status

Last updated: 2026-03-06 02:10 America/Chicago
Status owner: Cascade

## Program State
- Active Phase: 3 (Message Bus & Generalized Delegation)
- Active Sub-Phase: 4a (Enhanced Corporation View)
- Overall: Phase 3 complete (3a+3b+3c+3d) — factory fleet operational with persistent registry + self-update
- Blocking Issues: None
- **Key Discovery (2026-03-06):** A complete VP external worker system (`src/universal_agent/vp/`) was built since the plan was created. This partially supersedes Phase 3a — the generalized consumer pattern already exists for same-machine dispatch via SQLite. What remains is bridging Redis Streams (cross-machine) → local VP SQLite.

## Phase Status Summary

| Phase | Name | Status | Notes |
|---|---|---|---|
| 1 | Evergreen Headquarters | ✅ Done | Single-node HQ on VPS stable |
| 2 | Foundation & Parameterization | ✅ Done | Infisical, FACTORY_ROLE, capability gating, ops tokens, local worker bridge |
| 3a | Generalized Mission Consumer | ✅ Done | VP worker system + Redis→SQLite bridge (`redis_vp_bridge.py` + `redis_vp_result_bridge.py`) |
| 3b | Factory Heartbeat Protocol | ✅ Done | `FactoryHeartbeat` 60s POSTs + SQLite-backed `FactoryRegistry` + staleness enforcement + HQ self-heartbeat; 32 tests |
| 3c | Local Factory Deployment | ✅ Done | Infisical env + bridge + systemd service running on mint-desktop; E2E validated |
| 3d | Factory Template & Self-Update | ✅ Done | `update_factory.sh` + `system_handlers.py` + `POST /api/v1/ops/factory/update`; 13 tests |
| 4a | Enhanced Corporation View | ✅ Done | Color-coded freshness/latency, expandable rows, delegation history table, `GET /api/v1/ops/delegation/history` |
| 4b | Cost Analytics | 🔴 Not Started | ZAI telemetry aggregation across fleet |
| 4c | CSI-to-HQ Bridge | 🔴 Not Started | Wire CSI opportunity events through delegation bus |
| 5a | Memo Promotion Pipeline | 🔴 Not Started | Local factory → HQ knowledge base sync |
| 5b | Database Federation | 🔴 Not Started | Global vs Local state boundary enforcement |

## Progress Board

| Workstream | State | Evidence |
|---|---|---|
| `FactoryRole` enum + `FactoryRuntimePolicy` | Done | `src/universal_agent/runtime_role.py` — 3 roles, policy builder, LLM override |
| Runtime bootstrap wiring | Done | `src/universal_agent/runtime_bootstrap.py` — secrets + policy + LLM |
| Infisical centralized secrets | Done | `src/universal_agent/infisical_loader.py` — 4 unit tests pass |
| Gateway HTTP role enforcement | Done | Middleware blocks LOCAL_WORKER routes; 5 auth/role surface tests pass |
| Ops token issuance (HQ-only JWT) | Done | `POST /auth/ops-token` — 1hr TTL, HQ-gated |
| `MissionEnvelope` + `MissionResultEnvelope` schema | Done | `src/universal_agent/delegation/schema.py` — Pydantic models |
| `RedisMissionBus` (publish/consume/ack/DLQ) | Done | `src/universal_agent/delegation/redis_bus.py` — tested |
| Redis Docker infrastructure | Done | `corporation/infrastructure/redis/` — compose, conf, deploy README |
| Redis deploy script | Done | `scripts/install_vps_redis_bus.sh` |
| Factory capabilities API | Done | `GET /api/v1/factory/capabilities` — live on VPS |
| Factory registration API | Done | `GET/POST /api/v1/factory/registrations` — live on VPS, HQ-only |
| Corporation View UI | Done | `web-ui/app/dashboard/corporation/page.tsx` — deployed |
| HQ nav gating | Done | Corporation View only visible when `FACTORY_ROLE=HEADQUARTERS` |
| Agent capability gating | Done | `agent_setup.py` reads `FACTORY_ROLE` + `ENABLE_VP_CODER` |
| Tutorial worker (Redis transport) | Done | `scripts/tutorial_local_bootstrap_worker.py --transport redis` validated |
| **VP External Worker System** | **Done (Track B)** | `src/universal_agent/vp/` — VpWorkerLoop, dispatcher, clients (CODIE + Generalist), profiles, 15+ feature flags |
| **VP Mission Lifecycle (SQLite)** | **Done (Track B)** | `durable/state.py` — queue/claim/heartbeat/finalize/events via `vp_missions`/`vp_sessions` tables |
| **SessionContext Concurrency** | **Done (Track B)** | 6-phase refactor: ContextVar isolation, per-session locks, 24 tests |
| **GWS MCP Bridge** | **Done (Track B)** | 195 Google Workspace tools via `gws` CLI MCP server |
| **Process Heartbeat** | **Done (Track B)** | `process_heartbeat.py` — OS-level liveness file for watchdog |
| **Threads CSI Channel** | **Done (Track B)** | Webhooks, publishing, semantic enrichment |
| **Todoist Integration** | **Done (Track B)** | Rich handoff skill, heartbeat injection |
| **Infisical Env Provisioning** | **Done** | `scripts/infisical_provision_factory_env.py` — automated environment cloning with overrides |
| **Infisical `kevins-desktop` Env** | **Provisioned** | `corporation/docs/INFISICAL_ENVIRONMENTS.md` — machine-named environment strategy |
| **Redis→SQLite Bridge (Inbound)** | **Done** | `src/universal_agent/delegation/redis_vp_bridge.py` — consumes Redis → inserts VP SQLite; 13 tests pass |
| **Redis→SQLite Bridge (Outbound)** | **Done** | `src/universal_agent/delegation/redis_vp_result_bridge.py` — publishes VP results → Redis; 7 tests pass |
| **Bridge Entry Point** | **Done** | `python -m universal_agent.delegation.bridge_main` — standalone process with --once flag |
| **Factory Heartbeat (HQ registration)** | **Done** | `src/universal_agent/delegation/heartbeat.py` — 60s POSTs to HQ; 14 tests pass |
| **SQLite Factory Registry** | **Done** | `src/universal_agent/delegation/factory_registry.py` — persistent store, replaces in-memory dict; 18 tests pass |
| **Staleness Enforcement** | **Done** | Background loop in gateway: 5min→stale, 15min→offline; auto-revive on heartbeat |
| **HQ Self-Heartbeat** | **Done** | 60s `_hq_self_heartbeat_loop` keeps HQ registration fresh |
| **Factory Self-Update Script** | **Done** | `scripts/update_factory.sh` — git pull, uv sync, optional restart |
| **System Mission Handlers** | **Done** | `src/universal_agent/delegation/system_handlers.py` — `system:update_factory` handler; 13 tests pass |
| **Factory Update HQ Endpoint** | **Done** | `POST /api/v1/ops/factory/update` — publishes update mission to Redis bus |
| **Infisical Machine Identity** | **Done** | `kevins-desktop` client ID provisioned; read-only access to secrets |
| **Live Factory Validation** | **Done** | E2E: Redis publish → bridge consume → SQLite insert → result publish → HQ heartbeat 200 OK |
| **Local Factory Deploy Script** | **Done** | `scripts/deploy_local_factory.sh` — clone, uv sync, .env, validate, systemd |
| **Local Factory systemd Service** | **Done** | `deployment/systemd-user/universal-agent-local-factory.service` |
| **Local Factory Setup Guide** | **Done** | `corporation/docs/LOCAL_FACTORY_SETUP.md` — prerequisites, machine identity, deploy, verify, troubleshoot |
| **Enhanced Corporation View** | **Done** | Color-coded freshness indicators (dot + text), latency color coding, click-to-expand factory detail, delegation history table with mission status pills |
| Memo promotion + DB federation | Not Started | Spec: `corporation/docs/phases/phase_5_memory_federation.md` |

## Validation Snapshot
- `uv run pytest tests/gateway/test_ops_auth_role_surface.py -q` => 5 passed
- `uv run pytest tests/gateway/test_ops_api.py -k "factory_capabilities_and_registration_endpoints or local_redis_dispatch" -q` => 2 passed
- `uv run pytest tests/delegation/ -q` => **65 passed** (bridge 20 + heartbeat 14 + registry 18 + system handlers 13)
- `cd web-ui && npm run build` includes `/dashboard/corporation`
- Live VPS Redis delegation loop: enqueue → consume → completed (tutorial bootstrap)
- Live HQ fleet endpoints: `GET /api/v1/factory/capabilities`, `GET /api/v1/factory/registrations`

## Open Risks
- ~~No generalized consumer exists~~ **MITIGATED:** VP worker system provides local consumer pattern; Redis→SQLite bridge needed for cross-machine.
- ~~Factory registrations are in-memory only~~ **RESOLVED:** SQLite-backed `FactoryRegistry` in `factory_registry.db`; survives gateway restarts.
- ~~No periodic heartbeat from workers to HQ~~ **RESOLVED:** `FactoryHeartbeat` sends 60s POSTs; kevins-desktop registered as `online`.
- Redis exposed to internet with `requirepass` only; UFW CIDR restriction must be maintained.
- ~~Two parallel dispatch systems (Redis Streams + VP SQLite) need to be bridged cleanly.~~ **RESOLVED:** `redis_vp_bridge.py` bridges Redis→SQLite; `redis_vp_result_bridge.py` bridges results back.

## Decisions Log

| ID | Decision | Date | Rationale |
|---|---|---|---|
| D-001 | `FACTORY_ROLE` is the canonical env var (not `IS_HEADQUARTERS`) | 2026-02-28 | Single enum, unknown values fail to `LOCAL_WORKER` |
| D-002 | Redis Streams over NATS for message bus | 2026-02-28 | Zero infra cost, Docker on existing VPS, sufficient for fleet size |
| D-003 | Infisical for centralized secrets | 2026-02-28 | Machine identities, environment scoping, Python SDK |
| D-004 | `capabilities.md` generated at startup only (not live-recomputed) | 2026-02-28 | Fail-closed sandbox per session |
| D-005 | Local factories use SQLite; HQ uses PostgreSQL for global state | 2026-02-28 | Avoid network saturation; formal memos cross boundary |
| D-006 | **Option B: Redis→SQLite bridge** for cross-machine delegation | 2026-03-06 | Redis for transport, local VP SQLite for execution; preserves clean separation |
| D-007 | **Infisical environments named by machine** (not by role) | 2026-03-06 | `kevins-desktop`, `vps_hq`, future `kevins-tablet`; allows per-machine overrides |
| D-008 | **Automated Infisical provisioning** via `scripts/infisical_provision_factory_env.py` | 2026-03-06 | Clone from `dev` with role-specific overrides; idempotent |

## Lessons Learned

| # | Lesson | Source |
|---|---|---|
| L-001 | Tutorial worker pattern (claim/start/process/result) is the reference for generalized consumer | Phase 2-3 |
| L-002 | Gateway self-registration on startup provides baseline fleet visibility | Phase 3-4 |
| L-003 | `_factory_capability_labels()` pattern is reusable for consumer self-description | Phase 3 |
| L-004 | **VP worker system supersedes Phase 3a consumer spec** — don't build from scratch, bridge existing | 2026-03-06 audit |
| L-005 | **Infisical is canonical**, not `.env.sample` — plan docs were wrong to reference `.env.factory.template` | 2026-03-06 audit |
| L-006 | **Track B work compounds** — 70 commits of HQ development added SessionContext, GWS, process heartbeat, VP workers, Threads — all affect the corporation plan | 2026-03-06 audit |

## Next Execution Step
- **Phase 4b:** Cost analytics — ZAI telemetry aggregation across fleet
- **Phase 4c:** CSI-to-HQ bridge — wire CSI opportunity events through delegation bus
