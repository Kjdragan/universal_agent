# Scheduling Runtime V2 Short Soak Readiness (2026-02-08)

## Scope
- Validate post-Phase 5 runtime behavior with projection + push enabled.
- Confirm API health, metrics, replay, stream-once, and calendar endpoints remain stable across repeated checks.
- Produce a reusable soak command path for the 24h gate.

## Configuration Used
- `UA_SCHED_EVENT_PROJECTION_ENABLED=1`
- `UA_SCHED_PUSH_ENABLED=1`
- `UA_ENABLE_CRON=1`
- `UA_CRON_MOCK_RESPONSE=1`
- Gateway port: `8002`

## Automation
- Script: `src/universal_agent/scripts/scheduling_v2_soak.py`
- Output: `OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/scheduling_v2_soak_short_2026-02-08.json`

Run executed:
```bash
uv run python src/universal_agent/scripts/scheduling_v2_soak.py \
  --base-url http://127.0.0.1:8002 \
  --duration-seconds 60 \
  --interval-seconds 15 \
  --timeout-seconds 6 \
  --out-json OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/scheduling_v2_soak_short_2026-02-08.json
```

## Result Summary
- Window (UTC): `2026-02-08T22:31:30.702673+00:00` to `2026-02-08T22:32:30.703044+00:00`
- Cycles: `4`
- Total checks: `24`
- Failures: `0`
- Overall: `PASS`

Endpoint metrics:
- `health`: 4 samples, 0 fail, avg `1.873ms`, max `4.079ms`
- `scheduling_runtime_metrics`: 4 samples, 0 fail, avg `0.934ms`, max `1.185ms`
- `session_continuity_metrics`: 4 samples, 0 fail, avg `0.736ms`, max `0.998ms`
- `calendar_events`: 4 samples, 0 fail, avg `538.979ms`, max `588.931ms`
- `scheduling_replay`: 4 samples, 0 fail, avg `4.279ms`, max `5.369ms`
- `scheduling_stream_once`: 4 samples, 0 fail, avg `2.605ms`, max `5.056ms`

## Readiness Assessment
- V2 rollout path is stable in short-run soak conditions.
- No immediate API or stream regression observed.
- Calendar feed latency is materially higher than other checks but stable and error-free in this run.

## Remaining Gate
- 24h soak is still required for final sustained readiness declaration.

24h command:
```bash
uv run python src/universal_agent/scripts/scheduling_v2_soak.py \
  --base-url http://127.0.0.1:8002 \
  --duration-seconds 86400 \
  --interval-seconds 30 \
  --timeout-seconds 8 \
  --out-json OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/scheduling_v2_soak_24h_2026-02-09.json
```
