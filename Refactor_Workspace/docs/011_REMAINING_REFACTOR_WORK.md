# Remaining Refactor & Integration Work Plan
**Date**: 2026-01-25
**Status**: Planning Phase

## Overview
With the Core Execution Engine and User Interfaces (CLI/Web) now aligned and verified, the focus shifts to **Integration** and **feature Parity**. We have the "plumbing" (Worker Pools, URW structure), but we need to ensure they are fully operational and integrated with the new `UniversalAgent`.

## 1. URW Harness Integration Validation
The `Refactor_Workspace` indicates "Stage 5: URW Integration" is complete, but we must verify it against the *current* agent state.
- **Goal**: Ensure `src/universal_agent/urw/integration.py` correctly instantiates the *current* `UniversalAgent` and routes events.
- **Tasks**:
  1.  **Audit `integration.py`**: Check if it uses the new `identity.resolve_user_id` logic (it likely doesn't yet).
  2.  **Run Output Validation**: Execute a small URW phase (e.g., "Plan a hello world") and verify events appear in the Web UI/Terminal via the Gateway.

## 2. Worker Pool & Durability
The `durable/worker_pool.py` module exists, but has it been tested with the *latest* `InProcessGateway`?
- **Goal**: Robust, resumable distributed execution.
- **Tasks**:
  1.  **Identity Update**: Ensure workers use proper identity resolution when claiming jobs.
  2.  **Gateway Connection**: distinct `ExternalGateway` vs `InProcessGateway` logic check for workers.
  3.  **Stress Test**: Queue 5 jobs and have the worker pool drain them.

## 3. "Clawdbot" Feature Integration
You mentioned integrating "best features of the Claude bot". Based on previous context, this likely refers to:
- **GitHub Integration**: Full PR review/commenting workflow?
- **Specific Tools**: Are there tools in `Clawdbot` (e.g., specific linters, checks) not yet in `UniversalAgent`?
- **Action**: We need to review the `Clawdbot` repo (if available/mapped) or your list of desired features to port them.

## 4. The "External Gateway"
- **Goal**: A standalone server process that survives Agent crashes.
- **Status**: `gateway_server.py` is built.
- **Tasks**:
  1.  **Deployment Config**: Ensure `Dockerfile` or startup scripts exist to run `gateway_server` purely as an API entry point.
  2.  **Client Switch**: Verify `main.py` (CLI) can talk to a running External Gateway (`--gateway-url` flag test).

## Next Steps Plan
1.  **Immediate**: Audit `urw/integration.py` for identity compliance (fixing the immediate architectural divergence).
2.  **Validation**: Run a "Mock URW" pass to prove the integration works.
3.  **Feature selection**: You (User) list specific "Clawdbot" features to port, or I scan the repo if available.
