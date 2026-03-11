# 011 - SDK Phase 5 Accelerated Canary Rollout (0.1.48)

## Date

- Generated: 2026-03-08 (America/Chicago)

## Scope

This runbook accelerates Phase 5 rollout for the Claude Agent SDK upgrade while preserving deterministic promotion gates.

Included feature tracks:

1. Phase 2 telemetry enrichments (`stop_reason`, typed task messages, hook identity enrichment).
2. Phase 3 SDK session history augmentation.
3. Phase 4 dynamic MCP runtime controls (admin-only).

Excluded feature tracks:

1. `/loop` and session-scoped cron scheduling.

## Single Command Canary Matrix

Quick profile (recommended first):

```bash
uv run python scripts/run_sdk_phase5_canary.py --profile quick
```

Full profile (after quick is green):

```bash
uv run python scripts/run_sdk_phase5_canary.py --profile full
```

Optional live smoke add-on:

```bash
uv run python scripts/run_sdk_phase5_canary.py --profile full --include-live-probe
```

Artifacts generated:

1. `artifacts/sdk_phase5_canary/latest.json`
2. `artifacts/sdk_phase5_canary/latest.md`
3. timestamped JSON/Markdown snapshots in the same directory

## Accelerated Promotion Sequence

Use shorter windows but keep explicit gates:

1. Canary 1 (Phase 2 only, internal/dev sessions): 2 hours.
2. Canary 2 (Phase 3 enabled for ops/admin): 2 hours.
3. Canary 3 (Phase 4 enabled for admin-only maintenance sessions): 2 hours.
4. Full enablement candidate: after all 3 canaries are green plus matrix pass.

## Promotion Gates (Must Pass)

1. `run_sdk_phase5_canary.py` required checks all pass.
2. Query failure rate does not increase versus pre-upgrade baseline.
3. MCP initialization error rate does not increase.
4. No regression in gateway streaming and session persistence behavior.
5. No increase in hook guardrail false blocks.

## Manual Verification Commands

SDK history endpoints (Phase 3):

```bash
curl -sS -H "Authorization: Bearer $UA_OPS_TOKEN" \
  "http://localhost:8002/api/v1/ops/sdk/sessions?limit=20"
```

```bash
curl -sS -H "Authorization: Bearer $UA_OPS_TOKEN" \
  "http://localhost:8002/api/v1/ops/sdk/sessions/<session_id>/messages?limit=50"
```

Dynamic MCP status/add/remove (Phase 4):

```bash
curl -sS -H "Authorization: Bearer $UA_OPS_TOKEN" \
  "http://localhost:8002/api/v1/ops/sessions/<session_id>/mcp"
```

```bash
curl -sS -X POST -H "Authorization: Bearer $UA_OPS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"server_name":"test_stdio","server_config":{"type":"stdio","command":"echo","args":["ok"]}}' \
  "http://localhost:8002/api/v1/ops/sessions/<session_id>/mcp"
```

```bash
curl -sS -X DELETE -H "Authorization: Bearer $UA_OPS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"server_name":"test_stdio"}' \
  "http://localhost:8002/api/v1/ops/sessions/<session_id>/mcp"
```

Startup version banner check:

```bash
journalctl -u universal-agent-gateway -n 200 --no-pager | rg "Claude Agent SDK runtime versions"
```

## Fast Rollback

Disable new tracks and restart services:

```bash
export UA_ENABLE_SDK_TYPED_TASK_EVENTS=0
export UA_ENABLE_SDK_SESSION_HISTORY=0
export UA_ENABLE_DYNAMIC_MCP=0
```

If needed, restore pre-upgrade lockfile and redeploy runtime environment.

## Go/No-Go Rule

GO only when:

1. Required canary matrix checks pass.
2. All 3 short canary windows complete without gate violations.

NO-GO when any required check fails or error-rate gates regress.
