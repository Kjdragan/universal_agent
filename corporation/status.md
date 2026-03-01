# Corporation Build-Out Status

Last updated: 2026-03-01 14:35 America/Chicago
Status owner: Cascade

## Program State
- Active Phase: 3 (Message Bus & Generalized Delegation)
- Active Sub-Phase: 3a (Generalized Mission Consumer) — NOT STARTED
- Overall: In progress
- Blocking Issues: None

## Phase Status Summary

| Phase | Name | Status | Notes |
|---|---|---|---|
| 1 | Evergreen Headquarters | ✅ Done | Single-node HQ on VPS stable |
| 2 | Foundation & Parameterization | ✅ Done | Infisical, FACTORY_ROLE, capability gating, ops tokens, local worker bridge |
| 3a | Generalized Mission Consumer | 🔴 Not Started | **NEXT UP** — generic consumer loop, task-type registry, handler dispatch |
| 3b | Factory Heartbeat Protocol | 🔴 Not Started | Periodic heartbeat sender, stale detection enforcement |
| 3c | Local Factory Deployment | 🔴 Not Started | Deploy script, systemd service, setup playbook |
| 3d | Factory Template & Self-Update | 🔴 Not Started | Update script, `system:update_factory` mission type |
| 4a | Enhanced Corporation View | 🔴 Not Started | Per-factory workload, delegation history, drill-down |
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
| Generalized mission consumer | Not Started | Spec: `corporation/docs/phases/phase_3a_generalized_consumer.md` |
| Factory heartbeat protocol | Not Started | Spec: `corporation/docs/phases/phase_3b_factory_heartbeat.md` |
| Local factory deployment playbook | Not Started | Spec: `corporation/docs/phases/phase_3c_local_factory_deployment.md` |
| Factory template & self-update | Not Started | Spec: `corporation/docs/phases/phase_3d_factory_template.md` |
| Enhanced Corporation View | Not Started | Spec: `corporation/docs/phases/phase_4_observability.md` |
| Memo promotion + DB federation | Not Started | Spec: `corporation/docs/phases/phase_5_memory_federation.md` |

## Validation Snapshot
- `uv run pytest tests/gateway/test_ops_auth_role_surface.py -q` => 5 passed
- `uv run pytest tests/gateway/test_ops_api.py -k "factory_capabilities_and_registration_endpoints or local_redis_dispatch" -q` => 2 passed
- `cd web-ui && npm run build` includes `/dashboard/corporation`
- Live VPS Redis delegation loop: enqueue → consume → completed (tutorial bootstrap)
- Live HQ fleet endpoints: `GET /api/v1/factory/capabilities`, `GET /api/v1/factory/registrations`

## Open Risks
- No generalized consumer exists — only the tutorial bootstrap worker uses Redis transport.
- Factory registrations are in-memory only (gateway restart clears them). No persistent registry.
- No periodic heartbeat from workers — `last_seen_at` only updates on explicit registration POST.
- Redis exposed to internet with `requirepass` only; UFW CIDR restriction must be maintained.

## Decisions Log

| ID | Decision | Date | Rationale |
|---|---|---|---|
| D-001 | `FACTORY_ROLE` is the canonical env var (not `IS_HEADQUARTERS`) | 2026-02-28 | Single enum, unknown values fail to `LOCAL_WORKER` |
| D-002 | Redis Streams over NATS for message bus | 2026-02-28 | Zero infra cost, Docker on existing VPS, sufficient for fleet size |
| D-003 | Infisical for centralized secrets | 2026-02-28 | Machine identities, environment scoping, Python SDK |
| D-004 | `capabilities.md` generated at startup only (not live-recomputed) | 2026-02-28 | Fail-closed sandbox per session |
| D-005 | Local factories use SQLite; HQ uses PostgreSQL for global state | 2026-02-28 | Avoid network saturation; formal memos cross boundary |

## Lessons Learned

| # | Lesson | Source |
|---|---|---|
| L-001 | Tutorial worker pattern (claim/start/process/result) is the reference for generalized consumer | Phase 2-3 |
| L-002 | Gateway self-registration on startup provides baseline fleet visibility | Phase 3-4 |
| L-003 | `_factory_capability_labels()` pattern is reusable for consumer self-description | Phase 3 |

## Next Execution Step
- Begin Phase 3a: Build generalized mission consumer per spec at `corporation/docs/phases/phase_3a_generalized_consumer.md`
