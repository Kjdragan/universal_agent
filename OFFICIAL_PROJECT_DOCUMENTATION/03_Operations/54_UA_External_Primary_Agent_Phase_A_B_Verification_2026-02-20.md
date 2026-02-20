# 54 UA External Primary Agent Phase A/B Verification (2026-02-20)

## Scope
Phase A/B verification for:
1. Generic VP control-plane refactor.
2. External worker runtime framework and mission claim lifecycle.

## Verified Changes
1. DB schema includes mission claim/cancel fields:
   - `mission_type`
   - `payload_json`
   - `priority`
   - `worker_id`
   - `claim_expires_at`
   - `cancel_requested`
2. New durable state APIs validated:
   - `queue_vp_mission`
   - `claim_next_vp_mission`
   - `heartbeat_vp_mission_claim`
   - `request_vp_mission_cancel`
   - `finalize_vp_mission`
3. New profile + dispatcher + worker runtime added:
   - `vp.profiles`
   - `vp.dispatcher`
   - `vp.worker_loop`
   - `vp.worker_main`
4. New clients added:
   - `claude_code_client`
   - `claude_generalist_client`

## Test Evidence
Command:
```bash
./.venv/bin/pytest -q tests/durable/test_vp_dispatcher.py tests/api/test_gateway_coder_vp_routing.py tests/gateway/test_ops_api.py
```

Result:
- `55 passed` (targeted suite for VP runtime/dispatch APIs)

## Notes
1. Full repo test matrix currently contains broader pre-existing failures unrelated to VP runtime scope; targeted VP verification passed.
2. External dispatch remains default-off until workers are started (`UA_VP_EXTERNAL_DISPATCH_ENABLED=1` required).
