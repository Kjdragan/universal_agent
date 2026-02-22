# CSI Phase 0/0.5 Validation (2026-02-22)

## Scope Covered

1. CSI isolated project scaffold under `CSI_Ingester/development`.
2. CSI core contract/signature/store/emitter/service skeleton.
3. UA endpoint wiring for `POST /api/v1/signals/ingest`.
4. UA request validation/auth/signature checks.
5. Internal dispatch bridge from accepted CSI YouTube events into existing `youtube/manual` hook routing.
6. Sandbox-safe wrapper/preflight scripts and exception policy.

## Key Implemented Files

1. `src/universal_agent/signals_ingest.py`
2. `src/universal_agent/gateway_server.py`
3. `src/universal_agent/hooks_service.py`
4. `CSI_Ingester/development/csi_ingester/*`
5. `CSI_Ingester/development/scripts/csi_dev_env.sh`
6. `CSI_Ingester/development/scripts/csi_run.sh`
7. `CSI_Ingester/development/scripts/csi_preflight.sh`
8. `CSI_Ingester/documentation/05_CSI_Sandbox_Permissions_And_Exceptions_2026-02-22.md`

## Verification Commands Executed

1. CSI unit suite:

```bash
PYTHONPATH=CSI_Ingester/development uv run pytest CSI_Ingester/development/tests/unit -q
```

Result: `10 passed`

2. UA ingest unit suite:

```bash
uv run pytest tests/unit/test_signals_ingest.py -q
```

Result: `8 passed`

3. Hook service suite (including internal dispatch additions):

```bash
uv run pytest tests/test_hooks_service.py -q
```

Result: `24 passed`

4. Gateway endpoint suite for signals ingest dispatch:

```bash
uv run pytest tests/gateway/test_signals_ingest_endpoint.py -q
```

Result: `2 passed`

5. CSI<->UA contract compatibility tests:

```bash
uv run pytest tests/contract/test_csi_ua_contract.py -q
```

Result: `3 passed`

6. Python compile pass:

```bash
python3 -m py_compile src/universal_agent/signals_ingest.py src/universal_agent/gateway_server.py src/universal_agent/hooks_service.py
python3 -m py_compile CSI_Ingester/development/csi_ingester/*.py CSI_Ingester/development/csi_ingester/adapters/*.py CSI_Ingester/development/csi_ingester/store/*.py CSI_Ingester/development/csi_ingester/emitter/*.py CSI_Ingester/development/scripts/*.py
```

Result: success

7. Preflight script:

```bash
cd CSI_Ingester/development
scripts/csi_run.sh scripts/csi_preflight.sh
```

Result: success (warnings expected when env secrets/API keys are unset)

## Remaining Work To Reach Full v1

1. Wire DLQ replay to production runbook path and validate against live UA endpoint.
2. Complete migration/cutover scripts for VPS systemd deployment.
3. Add integration test for CSI service long-running scheduler loop with mocked adapters/emitter.
4. Implement richer observability counters/histograms aligned to PRD Section 11.4 naming.
5. Execute parallel-run validation against legacy poller on VPS.

