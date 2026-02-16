# 03. Phase A CODIE (CODER VP) Observability Playbook

This playbook defines the minimal operator workflow for monitoring the persistent CODIE lane (`vp.coder.primary`) during Phase A rollout and steady-state operations.

## 1) Endpoint inventory

1. Internal ops endpoint (token-gated in hardened deployments):
   - `GET /api/v1/ops/metrics/coder-vp`
2. Dashboard-facing endpoint (safe wrapper for dashboard surfaces):
   - `GET /api/v1/dashboard/metrics/coder-vp`

Both endpoints are backed by runtime DB VP tables (`vp_sessions`, `vp_missions`, `vp_events`).

---

## 2) Query examples

## 2.1 Direct ops query (gateway)

```bash
curl -sS \
  -H "x-ua-ops-token: ${UA_OPS_TOKEN}" \
  "http://127.0.0.1:8002/api/v1/ops/metrics/coder-vp?vp_id=vp.coder.primary&mission_limit=100&event_limit=400"
```

## 2.2 Dashboard-facing query (gateway)

```bash
curl -sS \
  "http://127.0.0.1:8002/api/v1/dashboard/metrics/coder-vp?vp_id=vp.coder.primary&mission_limit=50&event_limit=200"
```

## 2.3 Focused fallback-rate check

```bash
curl -sS \
  -H "x-ua-ops-token: ${UA_OPS_TOKEN}" \
  "http://127.0.0.1:8002/api/v1/ops/metrics/coder-vp?vp_id=vp.coder.primary" \
  | jq '.fallback'
```

## 2.4 Latency snapshot (count / avg / p95 / max)

```bash
curl -sS \
  -H "x-ua-ops-token: ${UA_OPS_TOKEN}" \
  "http://127.0.0.1:8002/api/v1/ops/metrics/coder-vp?vp_id=vp.coder.primary" \
  | jq '.latency_seconds'
```

`latency_seconds` now includes `p50_seconds`, `p95_seconds`, `avg_seconds`, and `max_seconds`.

## 2.5 Event mix and recent failures

```bash
curl -sS \
  -H "x-ua-ops-token: ${UA_OPS_TOKEN}" \
  "http://127.0.0.1:8002/api/v1/ops/metrics/coder-vp?vp_id=vp.coder.primary&event_limit=500" \
  | jq '{event_counts, recent_events: [.recent_events[] | select(.event_type=="vp.mission.fallback" or .event_type=="vp.mission.failed")]}'
```

## 2.6 Session lifecycle and recovery signals

```bash
curl -sS \
  -H "x-ua-ops-token: ${UA_OPS_TOKEN}" \
  "http://127.0.0.1:8002/api/v1/ops/metrics/coder-vp?vp_id=vp.coder.primary&event_limit=500" \
  | jq '{session_event_counts, recovery, session_health, recent_session_events}'
```

---

## 3) Interpretation guide

## 3.1 Fallback rate

- `fallback.rate < 0.05` -> healthy for guarded rollout.
- `0.05 <= fallback.rate < 0.20` -> monitor; inspect recent fallback payload errors.
- `fallback.rate >= 0.20` -> investigate immediately; consider forcing fallback lane (`UA_CODER_VP_FORCE_FALLBACK=1`) until stable.

## 3.2 Mission latency

- Use `latency_seconds.p95_seconds` as primary SLO signal.
- Rising p95 with stable fallback rate usually indicates load/queue pressure.
- Rising p95 + rising fallback rate usually indicates runtime degradation or adapter instability.

## 3.3 Event profile

Expected normal sequence per mission:

1. `vp.mission.dispatched`
2. (optional) `vp.mission.progress`
3. `vp.mission.completed`

Escalation indicators:

- Frequent `vp.mission.fallback`
- Any sustained `vp.mission.failed`
- Missing completion events over multiple recent missions

## 3.4 Session health

- `session.status` should typically be `active` or `idle` during steady-state.
- Repeated `degraded` / `recovering` should trigger lease/recovery drill.
- `session == null` indicates registry/session bootstrap has not happened or runtime DB is unavailable.
- Use `recovery.success_rate` and `session_health.orphan_rate` as lane-local drift indicators.
- Track `session_event_counts` for expected lifecycle sequence (`vp.session.created` -> `vp.session.resumed` and rare `vp.session.degraded`).

---

## 4) Quick recovery checklist

1. Capture snapshot from `/api/v1/ops/metrics/coder-vp`.
2. Inspect top fallback payload errors in `recent_events`.
3. If fallback rate is high, force primary path temporarily:
   - set `UA_CODER_VP_FORCE_FALLBACK=1`
4. Validate user flow on primary path and ensure no regressions.
5. Re-enable VP lane only after fallback rate and p95 return to acceptable range.

---

## 5) Rollout evidence to record

For each guarded rollout window, record:

1. Query timestamp (`generated_at`)
2. Fallback metrics (`missions_with_fallback`, `missions_considered`, `rate`)
3. Latency metrics (`avg_seconds`, `p95_seconds`, `max_seconds`)
4. Any notable fallback/failure payload signatures
5. Decision taken (continue / hold / force fallback)

---

## 6) Automation helper (recommended)

Use the rollout capture helper to generate a JSON snapshot plus a copy/paste-ready markdown row for the evidence log.

### 6.1 Direct mode (in-process snapshot)

```bash
PYTHONPATH=src uv run python scripts/coder_vp_rollout_capture.py \
  --mode direct \
  --window-label "Shadow window (real cohort)" \
  --scope "vp.coder.primary real cohort" \
  --vp-id "vp.coder.primary"
```

### 6.2 HTTP mode (running gateway)

```bash
PYTHONPATH=src uv run python scripts/coder_vp_rollout_capture.py \
  --mode http \
  --gateway-url "http://127.0.0.1:8002" \
  --ops-token "${UA_OPS_TOKEN}" \
  --window-label "Limited cohort window #1" \
  --scope "vp.coder.primary limited cohort"
```

The script prints:

1. Structured JSON snapshot (fallback, latency, mission/event counts)
2. A markdown table row with an automatic decision (`HOLD_SHADOW`, `READY_FOR_LIMITED_COHORT_PILOT`, or sustained-monitoring decisions when profile is `sustained`)

If HTTP mode returns 401/403, provide an ops token (`--ops-token` or `UA_OPS_TOKEN`) and re-run.

Copy that row into:

- `DraganCorp/docs/operations/04_Phase_A_Controlled_Rollout_Evidence_Log.md`

---

## 7) Sustained default-on monitoring (low-cost cadence)

Once broadened rollout guardrails are met, run CODIE in default-on mode with lightweight checks:

### 7.1 Recommended cadence

1. **Active implementation windows:** every 30-60 minutes.
2. **Normal steady state:** 2-4 snapshots per day.
3. **After incidents/config changes:** immediate ad-hoc snapshot + one follow-up within 30 minutes.

### 7.2 Low-cost sustained snapshot command

```bash
PYTHONPATH=src uv run python scripts/coder_vp_rollout_capture.py \
  --mode http \
  --gateway-url "http://127.0.0.1:8002" \
  --assessment-profile sustained \
  --mission-limit 60 \
  --event-limit 180 \
  --window-label "Sustained default-on monitor cycle" \
  --scope "vp.coder.primary sustained default-on"
```

### 7.3 Sustained decision interpretation

- `SUSTAINED_DEFAULT_ON_HEALTHY`: keep default-on, continue scheduled checks.
- `SUSTAINED_WATCH`: increase snapshot frequency and inspect recent fallback payloads.
- `SUSTAINED_FORCE_FALLBACK`: temporarily set `UA_CODER_VP_FORCE_FALLBACK=1`, recover, then re-evaluate.

---

## 8) Modular monitoring pattern (for future VP lanes)

This approach is intentionally reusable:

1. Keep one metrics endpoint contract.
2. Reuse same capture command with different `--vp-id` and thresholds.
3. Standardize decisions (`HEALTHY` / `WATCH` / `FORCE_FALLBACK`) across lanes.
4. Keep evidence rows and control-center updates identical in structure.

That lets future VP lanes and factory runtimes adopt monitoring with minimal new tooling.
