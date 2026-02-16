# 04. Phase A Controlled Rollout Evidence Log

This log tracks guarded rollout checkpoints for CODER VP in Phase A.

## Rollout policy (Phase A)

1. Start in shadow/limited cohort mode.
2. Capture objective metrics per window from CODER VP observability endpoints.
3. Record fallback and latency trends before promoting traffic share.
4. Revert to forced fallback (`UA_CODER_VP_FORCE_FALLBACK=1`) when thresholds are exceeded.

---

## Evidence table

| Date (UTC) | Window | Scope | Endpoint snapshot refs | Fallback rate | p95 latency | Decision | Notes |
|---|---|---|---|---:|---:|---|---|
| 2026-02-16 | Kickoff prep | Pre-rollout baseline + instrumentation readiness | `GET /api/v1/ops/metrics/coder-vp`, `GET /api/v1/dashboard/metrics/coder-vp` | n/a | n/a | START_SHADOW_WINDOW | Playbook, dashboard widget, and recovery tests in place; begin first shadow observation window next run cycle. |
| 2026-02-16T14:03:52Z | Shadow baseline #1 | `vp.coder.primary` (live snapshot, no mission traffic yet) | `_vp_metrics_snapshot(vp_id="vp.coder.primary", mission_limit=50, event_limit=200)` | 0.000 | n/a | HOLD_SHADOW | `missions_considered=0`, `missions_with_fallback=0`, `mission_counts={}`. Need traffic-bearing shadow window before promotion decision. |
| 2026-02-16T14:06:29Z | Shadow window #2 (traffic-bearing simulation) | `vp.coder.primary` synthetic coding workload via gateway dispatch path (4 missions, 1 injected VP exception) | `_vp_metrics_snapshot(vp_id="vp.coder.primary", mission_limit=100, event_limit=500)` | 0.250 | 0.534s | HOLD_SHADOW | `mission_counts={completed:4}`, `event_counts={dispatched:4, completed:4, fallback:1}`. Fallback rate intentionally elevated by injected failure; run next window without fault injection before promotion. |
| 2026-02-16T14:07:42Z | Shadow window #3 (traffic-bearing clean simulation) | `vp.coder.primary` synthetic coding workload via gateway dispatch path (4 missions, no injected failures) | `_vp_metrics_snapshot(vp_id="vp.coder.primary", mission_limit=100, event_limit=500)` | 0.000 | 0.377s | READY_FOR_LIMITED_COHORT_PILOT | `mission_counts={completed:4}`, `event_counts={dispatched:4, completed:4}`. Clean window meets fallback/latency gates in simulation; proceed to limited real cohort observation window. |
| 2026-02-16 | Limited cohort window #1 (real capture attempt) | HTTP-mode query against local gateway (`/api/v1/ops/metrics/coder-vp`) | `scripts/coder_vp_rollout_capture.py --mode http` | n/a | n/a | BLOCKED_AUTH | Endpoint returned `401 Unauthorized` in current shell because `UA_OPS_TOKEN` was not set; rerun with `--ops-token`/`UA_OPS_TOKEN` to capture first non-synthetic row. |
| 2026-02-16T14:18:29Z | Limited cohort window #1 (real capture) | HTTP-mode query against local gateway (`/api/v1/ops/metrics/coder-vp`) with `.env` ops token loaded | `scripts/coder_vp_rollout_capture.py --mode http` | 0.000 | n/a | HOLD_SHADOW | Auth path confirmed working (`UA_OPS_TOKEN` loaded), but current real window has `missions_considered=0`; collect traffic-bearing real cohort window before promotion decision. |
| 2026-02-16T14:48:00Z | Limited cohort window #1 (execution probe) | Session API + WS execution probe against running gateway (`/api/v1/sessions` + `/stream`) | `aiohttp` probe scripts + `/api/v1/ops/logs/tail` + `/api/v1/sessions` | n/a | n/a | BLOCKED_EXECUTION_PIPELINE | Session creation + WS attach succeed with `UA_INTERNAL_API_TOKEN`, but execute turns stall (no `query_complete`), session remains `status=running` with `active_runs=1`, and `run.log` shows long-running Bash tool flow. No CODER VP mission traffic reaches metrics until stuck run is cleared and execution path stabilizes. |
| 2026-02-16T14:57:39Z | Limited cohort window #1 (real traffic verified) | Real gateway run after enabling CODER VP flags and rerunning short coding probe through `/api/v1/sessions` + WS execute | `scripts/coder_vp_rollout_capture.py --mode http` | 0.000 | 35.964s | READY_FOR_LIMITED_COHORT_PILOT | First non-synthetic mission observed: `mission_counts={completed:1}`, `event_counts={vp.mission.dispatched:1,vp.mission.completed:1}`. Key prerequisite: gateway must run with `UA_ENABLE_CODER_VP=1` (and no shadow/force-fallback override) during cohort capture. |
| 2026-02-16T15:19:33Z | Limited cohort window #2 (execution probe) | WS execute probe for second real cohort run with internal token auth | `aiohttp` WS probe + `/api/v1/ops/sessions/{id}/cancel` | n/a | n/a | PARTIAL_TIMEOUT_RECOVERED | Initial probe timed out waiting for `query_complete`; ops cancel endpoint returned `task_cancelled=false` but session reconciled to `active_runs=0`, confirming fallback cleanup path still unblocks stale run state. |
| 2026-02-16T15:21:04Z | Limited cohort window #2 (real traffic) | Follow-up real coding turn via session WS execute and HTTP rollout capture | `scripts/coder_vp_rollout_capture.py --mode http` | 0.000 | 38.305s | READY_FOR_LIMITED_COHORT_PILOT | Second traffic-bearing window remains healthy: `missions_considered=2`, `missions_with_fallback=0`, `mission_counts={completed:2}`, `event_counts={vp.mission.dispatched:2,vp.mission.completed:2}`. No fallback events observed across real cohort sample. |

---

## Promotion gates

Promote from shadow to broader traffic only when all conditions hold for at least one observation window:

1. Fallback rate remains below target threshold from playbook guidance.
2. No sustained `vp.mission.failed` pattern in recent events.
3. Latency p95 is stable within acceptable operational range.
4. No active regressions in gateway/session continuity suites.
