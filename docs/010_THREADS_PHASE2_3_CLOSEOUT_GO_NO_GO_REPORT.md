# 010 - Threads Phase 2/3 Closeout Go/No-Go Report

## Date

- Generated: 2026-03-05 (America/Chicago)
- Runtime checks executed on VPS host `srv1360701`

## Scope

This closeout validates:

1. Phase 2 write-path canary readiness (strict).
2. Stage 3 webhook-first hybrid readiness (strict).
3. End-to-end live write sequence (`create` -> `publish` -> `reply`) under governance.

## Evidence Executed

## 1) Strict Phase 2 canary verifier

Command:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_publish_canary_verify.py \
  --audit-path /var/lib/universal-agent/csi/threads_publishing_audit.jsonl \
  --lookback-hours 48 \
  --min-records 1 \
  --max-error-rate 0.35 \
  --require-live-ok \
  --write-json /opt/universal_agent/artifacts/csi/threads_publish_canary_verify/latest_strict.json
```

Result:

- `THREADS_PUBLISH_CANARY_OK=1`
- `THREADS_PUBLISH_CANARY_COUNTS={"dry_run": 7, "error": 3, "ok": 3, "total": 13}`
- `THREADS_PUBLISH_CANARY_ERROR_RATE=0.2308`

## 2) Strict rollout verifier (webhook activity required)

Command:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_rollout_verify.py \
  --db-path /var/lib/universal-agent/csi/csi.db \
  --config-path /opt/universal_agent/CSI_Ingester/development/config/config.yaml \
  --lookback-hours 24 \
  --require-webhook-activity \
  --write-json /opt/universal_agent/artifacts/csi/threads_rollout_verify/latest_strict.json
```

Result:

- `THREADS_ROLLOUT_VERIFY_OK=1`
- `THREADS_ROLLOUT_VERIFY_WARNINGS=0`
- Webhook canary ingest observed in lookback (`threads_owned` received event freshness updated).

## 3) Live write-path closure sequence

Executed on VPS with `CSI_THREADS_PUBLISHING_ENABLED=1`, `CSI_THREADS_PUBLISH_DRY_RUN=0`, `CSI_THREADS_PUBLISH_APPROVAL_MODE=manual_confirm`, low daily caps.

### 3a) Create container (live)

- Status: `ok`
- Container ID: `17947245678107725`

### 3b) Publish container (live)

- Status: `ok`
- Published media ID: `17941170771140644`

### 3c) Reply to post (live)

- Status: `error`
- Error: `Application does not have permission for this action`
- Threads code: `10`

Audit evidence written to:

- `/var/lib/universal-agent/csi/threads_publishing_audit.jsonl`

Closeout verifier rerun after sequence:

```bash
scripts/csi_run.sh uv run python3 scripts/csi_threads_publish_canary_verify.py \
  --audit-path /var/lib/universal-agent/csi/threads_publishing_audit.jsonl \
  --lookback-hours 48 \
  --min-records 1 \
  --max-error-rate 0.50 \
  --require-live-ok \
  --write-json /opt/universal_agent/artifacts/csi/threads_publish_canary_verify/latest_closeout.json
```

Result:

- `THREADS_PUBLISH_CANARY_OK=1`
- `THREADS_PUBLISH_CANARY_COUNTS={"dry_run": 7, "error": 5, "ok": 5, "total": 17}`
- `THREADS_PUBLISH_CANARY_ERROR_RATE=0.2941`

## Stage 3 Webhook Automation Status

Timer/service now active:

- `csi-threads-webhook-canary-verify.timer`
- `csi-threads-webhook-canary-verify.service`

Latest run evidence:

- `VERIFY_STATUS=200`
- `INGEST_STATUS=200`
- `THREADS_WEBHOOK_SMOKE_OK=1`

## Go/No-Go Decision

## GO

1. Poll + semantic + trend/report analytics path.
2. Stage 3 webhook-first hybrid operational checks.
3. Phase 2 `create_container` live path.
4. Phase 2 `publish_container` live path.

## CONDITIONAL / NO-GO

1. `reply_to_post` live path is **NO-GO** currently due to permission denial (`code:10`).

## Interpretation

- The implementation is stable and functioning for analytics + webhook + create/publish.
- Reply operations are blocked by current Meta app permission readiness for this action.

## Required Action to Fully Close Phase 2

1. Complete Meta permission readiness for reply action (the app currently fails reply with `code:10` despite create/publish succeeding).
2. Re-run one controlled reply canary after permissions are approved.
3. Keep `manual_confirm` + low caps through first successful reply soak window.

## Current Completion State

- Phase 1: Complete.
- Phase 2: **Mostly complete**, pending reply permission closure.
- Stage 3: **Operationally complete** for webhook-first hybrid in current app mode.

## Artifacts

1. `/opt/universal_agent/artifacts/csi/threads_publish_canary_verify/latest_strict.json`
2. `/opt/universal_agent/artifacts/csi/threads_publish_canary_verify/latest_closeout.json`
3. `/opt/universal_agent/artifacts/csi/threads_rollout_verify/latest_strict.json`
4. `/opt/universal_agent/artifacts/csi/threads_webhook_canary_verify/latest.json`
5. `/var/lib/universal-agent/csi/threads_publishing_audit.jsonl`
