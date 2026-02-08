# Scheduling Runtime V2 Operational Runbook (Cron + Heartbeat + Calendar)

Date: 2026-02-08  
Owner: Universal Agent Ops  
Status: Active (Development/Staging)

## 1. Purpose
This runbook defines how to roll out, validate, and operate Scheduling Runtime V2 safely:
- Event-driven projection path for calendar read model
- Push-first dashboard update path (SSE)
- Degraded-mode fallback polling
- Missed-event stasis controls (approve/reschedule/delete)

## 2. Runtime Flags
Backend flags:
- `UA_SCHED_EVENT_PROJECTION_ENABLED`
  - `0` = V1 feed path (direct runtime scan)
  - `1` = V2 projection feed path
- `UA_SCHED_PUSH_ENABLED`
  - `0` = disable scheduling SSE stream endpoint
  - `1` = enable scheduling SSE stream endpoint
- `UA_SCHED_EVENT_BUS_MAX`
  - bounded event buffer size (default `5000`)

Frontend flag:
- `NEXT_PUBLIC_UA_SCHED_PUSH_ENABLED`
  - `0` = disable push client, rely on fallback polling
  - `1` = enable push client

## 3. Rollout Sequence
1. Baseline (safe start)
- `UA_SCHED_EVENT_PROJECTION_ENABLED=0`
- `UA_SCHED_PUSH_ENABLED=0`
- `NEXT_PUBLIC_UA_SCHED_PUSH_ENABLED=0`

2. Enable projection first (shadow/compare window)
- `UA_SCHED_EVENT_PROJECTION_ENABLED=1`
- Keep push disabled.
- Validate calendar parity and projection metrics.

3. Enable push stream backend
- `UA_SCHED_PUSH_ENABLED=1`
- Keep frontend push disabled for one cycle to verify stream health via replay/metrics.

4. Enable push frontend
- `NEXT_PUBLIC_UA_SCHED_PUSH_ENABLED=1`
- Confirm dashboard push status is connected and fallback polling remains dormant in normal mode.

5. Steady state
- Projection + push enabled.
- Watch stasis queue and continuity metrics.

## 4. Operational Checks
Health/API checks:
- `GET /api/v1/health`
- `GET /api/v1/ops/metrics/scheduling-runtime`
- `GET /api/v1/ops/metrics/session-continuity`
- `GET /api/v1/ops/calendar/events?source=all&view=week`

Push checks:
- `GET /api/v1/ops/scheduling/events?since_seq=0&limit=50`
- `GET /api/v1/ops/scheduling/stream?since_seq=0&once=1`

Expected push counters to move:
- `push_replay_requests`
- `push_stream_connects`
- `push_stream_disconnects`
- `push_stream_event_payloads`
- `push_stream_keepalives`

## 5. SLO Gate (Dev/Staging)
Treat V2 as healthy when:
- Calendar correctness: no missing expected cron/heartbeat entries in active test windows.
- Push reliability: reconnect works and state refresh remains correct during disconnects.
- Missed-event lifecycle: approve/reschedule/delete actions reconcile correctly and stasis queue remains consistent.
- Continuity/heartbeat: heartbeat status and continuity metrics update during normal operations.

## 6. Rollback
Immediate safe rollback:
- Set `UA_SCHED_PUSH_ENABLED=0`
- Set `NEXT_PUBLIC_UA_SCHED_PUSH_ENABLED=0`
- If needed, set `UA_SCHED_EVENT_PROJECTION_ENABLED=0`

Behavior after rollback:
- Dashboard falls back to polling path.
- Calendar uses non-projection path when projection flag is off.
- Existing cron/heartbeat engines continue unchanged.

## 7. Troubleshooting
Issue: `Cron service not available`
- Confirm backend started with cron enabled and no startup crash in logs.
- Verify `GET /api/v1/cron/jobs` returns `200`.

Issue: Calendar shows stale missed entries after action
- Confirm action endpoint response is `200`.
- Re-fetch calendar feed and check `stasis_queue` entries.
- Validate event id and source mapping (`cron|...` or `heartbeat|...`).

Issue: Push not connecting
- Check `UA_SCHED_PUSH_ENABLED` and `NEXT_PUBLIC_UA_SCHED_PUSH_ENABLED`.
- Validate ops auth token propagation (`X-UA-OPS-TOKEN` or `ops_token` query path for EventSource).
- Inspect `push_stream_connects` / `push_stream_disconnects`.

Issue: Heartbeat panel not reflecting run state
- Verify selected/attached session id.
- Check `GET /api/v1/heartbeat/last?session_id=<id>`.
- Check busy flag and heartbeat event emissions in logs.

## 8. Notes
- Heartbeat remains the catch-all loop.
- Cron remains scheduler-of-record.
- Calendar is projection/view + controls, not a replacement scheduler.

## 9. Soak Automation
Use:
- `src/universal_agent/scripts/scheduling_v2_soak.py`

Example short run:
```bash
uv run python src/universal_agent/scripts/scheduling_v2_soak.py \
  --base-url http://127.0.0.1:8002 \
  --duration-seconds 300 \
  --interval-seconds 15 \
  --timeout-seconds 6 \
  --out-json OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/scheduling_v2_soak_short.json
```

Example 24h gate run:
```bash
uv run python src/universal_agent/scripts/scheduling_v2_soak.py \
  --base-url http://127.0.0.1:8002 \
  --duration-seconds 86400 \
  --interval-seconds 30 \
  --timeout-seconds 8 \
  --out-json OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/scheduling_v2_soak_24h.json
```
