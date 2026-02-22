# CSI Local E2E Smoke And Deployment Readiness (2026-02-22)

## Scope Covered

1. Local signed CSI -> UA ingest smoke using FastAPI app startup path.
2. CSI integration smoke test in pytest.
3. Full CSI + UA regression subset before VPS deployment planning.
4. VPS deployment runbook and smoke tooling readiness.

## Commands Executed

1. Local smoke script:

```bash
uv run python CSI_Ingester/development/scripts/csi_local_e2e_smoke.py
```

Observed: `SMOKE_OK status=200 accepted=1 internal_dispatches=1`

2. CSI integration smoke test:

```bash
uv run pytest CSI_Ingester/development/tests/integration/test_csi_to_ua_local_smoke.py -q
```

Observed: `1 passed`

3. Regression subset:

```bash
PYTHONPATH=src:CSI_Ingester/development uv run pytest \
  CSI_Ingester/development/tests/unit \
  CSI_Ingester/development/tests/integration/test_csi_to_ua_local_smoke.py \
  tests/unit/test_signals_ingest.py \
  tests/gateway/test_signals_ingest_endpoint.py \
  tests/contract/test_csi_ua_contract.py \
  tests/test_hooks_service.py -q
```

Observed: `51 passed`

## New Operational Assets

1. `CSI_Ingester/development/scripts/csi_emit_smoke_event.py`
2. `CSI_Ingester/documentation/06_CSI_VPS_Deployment_Runbook_v1_2026-02-22.md`

## Outcome

1. Local end-to-end contract path is validated.
2. Deployment strategy is now documented with executable smoke checks and rollback path.

