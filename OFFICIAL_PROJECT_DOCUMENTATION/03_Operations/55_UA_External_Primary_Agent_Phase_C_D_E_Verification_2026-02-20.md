# 55 UA External Primary Agent Phase C/D/E Verification (2026-02-20)

## Scope
Phase C/D/E verification for:
1. CODIE hard isolation guardrails.
2. Generalist primary agent profile/runtime enablement.
3. VP API operations and compatibility wrappers.

## Verified Changes
1. CODIE hard path isolation:
   - Dispatch-time validation blocks UA repo/runtime roots.
   - Worker-time validation blocks unsafe CODIE target paths.
   - Handoff allowlist supported via `UA_VP_HANDOFF_ROOT`.
2. Generalist VP enablement:
   - `vp.general.primary` profile present.
   - `GENERALIST_VP_SOUL.md` added.
   - Mission execution path added through external worker client.
3. New ops endpoints:
   - `GET /api/v1/ops/metrics/vp`
   - `GET /api/v1/ops/vp/sessions`
   - `GET /api/v1/ops/vp/missions`
   - `POST /api/v1/ops/vp/missions/dispatch`
   - `POST /api/v1/ops/vp/missions/{mission_id}/cancel`
4. Compatibility wrappers retained:
   - `GET /api/v1/ops/metrics/coder-vp`
   - `GET /api/v1/dashboard/metrics/coder-vp`

## Test Evidence
Focused checks included:
1. Dispatch/list/cancel flow over new VP endpoints.
2. CODIE and GENERALIST mission rows visible in generic metrics.
3. Existing coder metrics wrapper behavior preserved.

Command:
```bash
./.venv/bin/pytest -q tests/durable/test_vp_dispatcher.py tests/api/test_gateway_coder_vp_routing.py tests/gateway/test_ops_api.py
```

Result:
- `55 passed`

## Rollout Guidance
1. Deploy with external dispatch disabled:
   - `UA_VP_EXTERNAL_DISPATCH_ENABLED=0`
2. Start worker services:
   - `scripts/start_vp_worker.sh vp.general.primary`
   - `scripts/start_vp_worker.sh vp.coder.primary`
3. Enable canary:
   - `UA_VP_EXTERNAL_DISPATCH_ENABLED=1`
4. Monitor:
   - queue depth and mission status transitions
   - fallback rate
   - cancel request reliability
