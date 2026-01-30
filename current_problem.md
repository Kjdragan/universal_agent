# Current Problem: Heartbeat Service Hangs on Execution Initialization

## Summary
The Heartbeat Service correctly detects that a heartbeat is needed and attempts to trigger it. However, the execution flow hangs indefinitely during the initialization of the `ClaudeSDKClient` (specifically, when checking/starting the bundled CLI subprocess). The heartbeat never completes, and the test client eventually times out.

## Symptoms
1.  **Triggering works**: The `HeartbeatService` scheduler loop works. It finds the session and `HEARTBEAT.md`, logs `💓 Triggering heartbeat for ...`.
2.  **Execution starts**: The system enters the execution path. Use of the bundled CLI is logged:
    ```
    INFO:claude_agent_sdk._internal.transport.subprocess_cli:Using bundled Claude Code CLI: ...
    ```
3.  **Hang**: The process hangs at this point.
    *   Debug prints placed *inside* the `async with ClaudeSDKClient(...)` block in `src/universal_agent/execution_engine.py` are **never reached**.
    *   This indicates `client.__aenter__()` (or the underlying transport startup) never returns.
4.  **Timeout**: The `test_heartbeat.py` client times out waiting for the `heartbeat_summary` broadcast.

## Reproduction Steps
Use the following commands to reproduce the issue:

```bash
# 1. Start Gateway Server with Heartbeat enabled
export UA_ENABLE_HEARTBEAT=1
export UA_GATEWAY_PORT=8021
uv run python -m universal_agent.gateway_server

# 2. In another terminal, run the test client
export GATEWAY_URL=http://localhost:8021
uv run tests/gateway/test_heartbeat.py
```

## Key Files & Locations
*   **`src/universal_agent/heartbeat_service.py`**:
    *   `_process_session`: Calling `_run_heartbeat`.
    *   `_run_heartbeat`: Calling `self.gateway.execute(session, request)`.
*   **`src/universal_agent/gateway.py`**:
    *   `InProcessGateway.execute`: Delegates to `ProcessTurnAdapter`.
*   **`src/universal_agent/execution_engine.py`**:
    *   `ProcessTurnAdapter.execute`: Initializes `ClaudeSDKClient`.
    *   **CRITICAL LOCATION**: Line ~190 `async with ClaudeSDKClient(self._options) as client:`. The hang happens here.

## Investigation Notes
*   **Gateway Environment**: The gateway is running under `uv run`. The `ClaudeSDKClient` attempts to spawn a subprocess (the "bundled Claude Code CLI").
*   **Subprocess Issue?**: It is highly likely that the subprocess is failing to start, deadlocking, or waiting for input/signals that are not being handled correctly in the Gateway Server's `asyncio` loop configuration.
*   **Debugging Attempts**:
    *   Increased timeout to 60s (didn't help).
    *   Added extensive logging (confirmed the hang location).
    *   Verified simple broadcast works (Phase 1 was successful).

## Next Steps for Investigation
1.  **Isolate `ClaudeSDKClient`**: Create a minimal script that just tries to initialize `ClaudeSDKClient` and run a "hello world" prompt inside the `uv run python -m universal_agent.gateway_server` environment (or similar) to confirm if it's the environment or the `HeartbeatService` logic.
2.  **Check Subprocess Output**: The subprocess stdout/stderr might be getting swallowed or piped incorrectly. Need to verify where the CLI's output is going.
3.  **Transport Configuration**: Check if `spawn` vs `fork` or other process details are causing issues in this environment.
4.  **CLI Bundling**: Verify the path to the bundled CLI is correct and executable in this context.

This needs to be solved to allow the Heartbeat Service to actually *run* the agent logic.

---

## ✅ Resolution (2026-01-29)

### Root causes
1. **Gateway did not load `.env`**, so the Claude CLI subprocess launched by `ClaudeSDKClient` inherited no API keys. This caused the SDK initialization to stall under the gateway server environment.
2. **Heartbeat summary was suppressed** whenever the agent returned `UA_HEARTBEAT_OK`, which made `tests/gateway/test_heartbeat.py` time out (it expects a `heartbeat_summary` event).

### Fixes applied
- **Load `.env` at gateway startup** so all subprocesses inherit credentials.  
  File: `src/universal_agent/gateway_server.py`
- **Always broadcast `heartbeat_summary`**, even when `UA_HEARTBEAT_OK` is returned (now includes `ok_only: true`).  
  File: `src/universal_agent/heartbeat_service.py`

### Validation
Re-ran the original repro steps and the test now passes:
```bash
export UA_ENABLE_HEARTBEAT=1
export UA_GATEWAY_PORT=8021
uv run python -m universal_agent.gateway_server

export GATEWAY_URL=http://localhost:8021
uv run tests/gateway/test_heartbeat.py
```
Result: ✅ `heartbeat_summary` received within timeout.

---

## ⚠️ Update (2026-01-29 late) — Verification Failed in Some Environments

### Observed behavior
- Gateway logs show:
  - `DEBUG: ProcessTurnAdapter starting engine task...`
  - **No subsequent events or output**
- The client waits indefinitely and times out.
- This suggests a deeper hang in `process_turn` / CLI subprocess I/O or initialization.

### Additional mitigations + instrumentation added
- **CLI stderr capture** in `ProcessTurnAdapter` to surface trust/auth prompts or CLI errors:
  - Env: `UA_CLAUDE_CLI_STDERR=1` (default on)
  - Optional: `UA_CLAUDE_CLI_DEBUG=api,hooks` to pass CLI debug categories
  - File: `src/universal_agent/execution_engine.py`
- **Heartbeat execution timeout** to prevent infinite waits:
  - Env: `UA_HEARTBEAT_EXEC_TIMEOUT` (default 45s)
  - On timeout, heartbeat emits `UA_HEARTBEAT_TIMEOUT` via `heartbeat_summary`
  - File: `src/universal_agent/heartbeat_service.py`

### Next debugging steps
1. Re-run gateway + test with CLI stderr capture enabled and collect any `Claude CLI stderr:` lines.
2. If no stderr appears, check whether the CLI is waiting for a trust prompt or blocked on network.
3. If hang persists, consider a fallback for heartbeat execution using `claude_agent_sdk.query()` (print mode) to bypass interactive initialization.
