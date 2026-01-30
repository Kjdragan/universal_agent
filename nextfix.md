# Heartbeat Delivery Test Fix (Shareable Summary)

## Problem
The integration test `tests/gateway/test_heartbeat_delivery.py` hung at “Starting Gateway…” and later timed out waiting for heartbeat events. The gateway subprocess logs showed it was running, yet the test still failed.

## Root Causes
1. **Health endpoint mismatch**
   - The test’s readiness probe called `http://127.0.0.1:8031/health`, but the gateway’s health endpoint is `http://127.0.0.1:8031/api/v1/health`.
   - Result: the test believed the server never started, even though it was up.

2. **Heartbeat execution depended on live Claude CLI**
   - The delivery policy test should validate *event delivery* (show_ok, dedupe), not LLM execution.
   - In subprocess/CI runs, the Claude CLI can block (auth/trust/TTY). That caused missed `heartbeat_summary` events and timeouts.

## Fixes Applied
### 1) Correct health check
**File:** `tests/gateway/test_heartbeat_delivery.py`
- `wait_for_server()` now hits `/api/v1/health` instead of `/health`.

### 2) Deterministic heartbeat response for tests
**File:** `src/universal_agent/heartbeat_service.py`
- Added **test-only mock mode** controlled by env var: `UA_HEARTBEAT_MOCK_RESPONSE=1`.
- When enabled, heartbeat returns deterministic tokens based on `HEARTBEAT.md` content:
  - `UA_HEARTBEAT_OK`, `ALERT_TEST_A`, `ALERT_TEST_B`, or a quoted string.
- This keeps the test focused on delivery policies and avoids flakiness from the CLI.

**File:** `tests/gateway/test_heartbeat_delivery.py`
- Set `UA_HEARTBEAT_MOCK_RESPONSE=1` for all three gateway runs.

## Why This Works
- The server readiness check now matches the real API path.
- The test no longer depends on external model execution, which is irrelevant for delivery-policy verification.
- Production behavior is unchanged because the mock is **opt‑in** only.

## Verification
Command:
```bash
uv run tests/gateway/test_heartbeat_delivery.py
```
Result: **✅ ALL TESTS PASSED**

## Notes
- The mock mode is only for tests/CI and can be disabled by omitting `UA_HEARTBEAT_MOCK_RESPONSE`.
- Delivery logic (show_ok, dedupe) is still exercised fully against real WebSocket broadcasts.
