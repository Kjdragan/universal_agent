# Document 05. CSI Sandbox Permissions And Exceptions (2026-02-22)

## 1. Purpose

Define a stable execution policy for CSI development and operations so routine work does not fail on sandbox/cache/path restrictions, while still enforcing safe escalation boundaries.

This policy applies to:

1. `CSI_Ingester/development/*` local development.
2. UA integration work in `src/universal_agent/*` for CSI ingest endpoint.
3. Controlled remote deployment/ops actions for CSI services.

## 2. Default Execution Mode (No Escalation)

All day-to-day CSI work should run in non-escalated mode and stay inside repository writable roots.

Allowed by default:

1. Create/edit files under:
   1. `CSI_Ingester/*`
   2. `src/universal_agent/*` (for CSI ingest integration)
   3. `tests/*` (for CSI/UA contract tests)
2. Run local commands:
   1. `uv run ...`
   2. `pytest`
   3. `python3 -m py_compile ...`
   4. `rg`, `sed`, `cat`, `find`, `ls`

Required environment profile for local commands:

1. `UV_CACHE_DIR=/tmp/uv-cache`
2. `TMPDIR=/tmp`
3. `XDG_CACHE_HOME=/tmp/.cache`

These defaults avoid permission errors from user-home cache paths during sandboxed runs.

## 3. CSI Sandbox Helper Scripts

Use these scripts for consistent local execution:

1. `CSI_Ingester/development/scripts/csi_dev_env.sh`
   1. Exports sandbox-safe cache/temp env vars.
2. `CSI_Ingester/development/scripts/csi_run.sh`
   1. Wraps command execution with the same sandbox-safe environment.

Examples:

```bash
source CSI_Ingester/development/scripts/csi_dev_env.sh
CSI_Ingester/development/scripts/csi_run.sh uv run --group dev pytest tests/unit/test_signature.py -q
CSI_Ingester/development/scripts/csi_run.sh uv run uvicorn csi_ingester.app:app --host 127.0.0.1 --port 8091
```

## 4. Escalation Exceptions (Allowed With Explicit Approval)

Escalation is allowed only for the following CSI activities.

## 4.1 Remote/VPS Operations

Examples:

1. `ssh -i ~/.ssh/id_ed25519 root@...`
2. `systemctl` operations on VPS.
3. Writing CSI service files under `/opt/universal_agent` on VPS.

Reason:

1. These act outside local writable roots.

## 4.2 Production Service Management

Examples:

1. Installing/enabling `csi-ingester.service`.
2. Updating production env files.
3. Restarting or tailing production logs.

Reason:

1. Requires host-level permissions and affects running services.

## 4.3 Network-Dependent Integration Checks

Examples:

1. Live calls to external YouTube APIs during deployment validation.
2. Live CSI->UA requests to production endpoints.

Reason:

1. Network restrictions may block these in local sandbox mode.

## 5. Explicitly Restricted Actions

The following remain restricted unless explicitly requested and approved by operator:

1. Destructive filesystem commands (`rm -rf`, mass deletes outside CSI scope).
2. Reverting unrelated repo changes.
3. Any write outside approved project paths without a clear CSI task need.

## 6. Auth And Secret Boundary Policy

CSI integration must keep auth secrets isolated.

1. CSI->UA path uses dedicated secrets:
   1. `CSI_UA_SHARED_SECRET` (CSI side)
   2. `UA_SIGNALS_INGEST_SHARED_SECRET` (UA side)
2. Do not reuse:
   1. `UA_HOOKS_TOKEN`
   2. `COMPOSIO_WEBHOOK_SECRET`

## 7. PRD/Implementation Alignment Guard

Before each implementation phase, verify:

1. Signature format is still locked to:
   1. `X-CSI-Signature: sha256=<hmac_hex>`
2. UA endpoint remains:
   1. `POST /api/v1/signals/ingest`
3. UA wiring/handler boundary remains:
   1. route in `src/universal_agent/gateway_server.py`
   2. logic in `src/universal_agent/signals_ingest.py`

## 8. Operator Checklist

Use this checklist for CSI sessions:

1. Source `csi_dev_env.sh` (or use `csi_run.sh`).
2. Run local tests non-escalated first.
3. Request escalation only for remote/VPS actions.
4. Keep CSI secrets isolated from existing hook/composio secrets.
5. Record any new exception in this document.

