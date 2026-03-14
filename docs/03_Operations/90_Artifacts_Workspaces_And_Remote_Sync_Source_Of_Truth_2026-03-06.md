# 90. Artifacts, Workspaces, and Remote Sync Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for where Universal Agent stores session workspaces and durable artifacts, how local versus mirrored storage roots are exposed through the API, and how remote VPS workspace/artifact sync currently works.

## Executive Summary

The current storage model has three distinct layers:

1. **canonical local session workspaces** under `AGENT_RUN_WORKSPACES`
2. **canonical local durable artifacts** under `UA_ARTIFACTS_DIR` or repo `artifacts`
3. **optional mirrored remote VPS copies** under `remote_vps_workspaces` and `remote_vps_artifacts`

The most important rule is:
- local workspaces and local artifacts are the canonical storage roots for the current running node
- mirrored VPS storage is a debugging and inspection surface, not the source of truth for active local runtime execution

## Current Canonical Storage Roots

Primary implementation:
- `src/universal_agent/api/server.py`

Current local canonical roots:
- workspaces -> `<repo>/AGENT_RUN_WORKSPACES`
- artifacts -> `UA_ARTIFACTS_DIR` if set, else `<repo>/artifacts`

Current default mirror roots:
- workspaces mirror -> `AGENT_RUN_WORKSPACES/remote_vps_workspaces`
- artifacts mirror -> `artifacts/remote_vps_artifacts`

Configured by env:
- `UA_VPS_WORKSPACES_MIRROR_DIR`
- `UA_VPS_ARTIFACTS_MIRROR_DIR`

## 1. Session Workspaces

Primary implementation:
- `src/universal_agent/api/server.py`
- `src/universal_agent/agent_setup.py`
- `src/universal_agent/workspace/bootstrap.py`
- various runtime session producers

A session workspace is the main per-session storage directory.

Current common contents include:
- `transcript.md`
- `run.log`
- `trace.json`
- session-specific scratch files
- `work_products/`
- sometimes `memory/`

### Session Identity Patterns

Current session-id source inference uses prefixes such as:
- `session_` -> chat/web
- `session_hook_` / `session-hook_` -> webhook/hook
- `tg_` -> telegram
- `api_` -> API
- `cron_` -> cron
- `vp_` -> VP-related

These prefixes are used both for UI labeling and for sync/ops handling.

## 2. Durable Artifacts

Primary implementation:
- `src/universal_agent/api/server.py`
- tooling and guardrails elsewhere in the repo

Durable artifacts are intended for outputs that should outlive a single ephemeral run flow.

Current artifacts root:
- `UA_ARTIFACTS_DIR`
- fallback `<repo>/artifacts`

The API server exposes this durable root through:
- `GET /api/artifacts`
- `GET /api/artifacts/files/{file_path}`

Important distinction:
- session workspaces are per-session execution context
- artifacts root is the durable shared output surface

## 3. API Storage Root Model

Primary implementation:
- `src/universal_agent/api/server.py`

The API server currently supports two storage-root modes:
- `root_source=local`
- `root_source=mirror`

### Meaning

For scope `workspaces`:
- `local` -> canonical `AGENT_RUN_WORKSPACES`
- `mirror` -> `UA_VPS_WORKSPACES_MIRROR_DIR`

For scope `artifacts`:
- `local` -> canonical `UA_ARTIFACTS_DIR` or local `artifacts`
- `mirror` -> `UA_VPS_ARTIFACTS_MIRROR_DIR`

This split is important because many ops and dashboard surfaces intentionally let the operator compare local source-of-truth storage to mirrored VPS copies.

## 4. Storage and Browser APIs

Primary implementation:
- `src/universal_agent/api/server.py`
- `web-ui/lib/sessionDirectory.ts`

Current workspace/file endpoints include:
- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `GET /api/files?session_id=...`
- `GET /api/files/{session_id}/{file_path}`

Current VPS/mirror storage endpoints include:
- `GET /api/vps/sync/status`
- `POST /api/vps/sync/now`
- `GET /api/vps/storage/sessions`
- `GET /api/vps/storage/artifacts`
- `GET /api/vps/storage/overview`
- `GET /api/vps/files`
- `GET /api/vps/file`
- `POST /api/vps/files/delete`

Current session directory UI path:
- dashboard session listing prefers `/api/v1/ops/sessions`
- falls back to legacy `/api/v1/sessions` in local-only mode

## 5. Remote VPS Sync Model

Primary implementation:
- `scripts/sync_remote_workspaces.sh`
- `scripts/pull_remote_workspaces_now.sh`
- `src/universal_agent/api/server.py`
- `scripts/install_remote_workspace_sync_timer.sh`
- `scripts/remote_workspace_sync_control.sh`

Current sync purpose:
- mirror remote VPS session workspaces and durable artifacts locally for debugging, inspection, and incident response

This is not intended to make the mirror authoritative for active runtime state.

### Current Sync Inputs

Remote-side defaults include:
- remote workspaces -> `/opt/universal_agent/AGENT_RUN_WORKSPACES`
- remote artifacts -> `/opt/universal_agent/artifacts`

Local mirror defaults include:
- local workspaces mirror -> `AGENT_RUN_WORKSPACES/remote_vps_workspaces`
- local artifacts mirror -> `artifacts/remote_vps_artifacts`

### Current Auth/Connectivity Model

Sync currently supports:
- `keys`
- `tailscale_ssh`

Tailnet preflight can be enforced through:
- `UA_TAILNET_PREFLIGHT`
- `UA_SKIP_TAILNET_PREFLIGHT`

## 6. Ready-Marker Gating

Primary implementation:
- `scripts/sync_remote_workspaces.sh`

One of the most important current sync safety rules is ready-marker gating.

Defaults:
- enabled by default
- marker file name defaults to `sync_ready.json`
- ready minimum age defaults to `45` seconds
- default gated prefixes are `session_` and `tg_`

Current control env:
- `UA_REMOTE_SYNC_REQUIRE_READY_MARKER`
- `UA_REMOTE_SYNC_READY_MARKER_FILENAME`
- `UA_REMOTE_SYNC_READY_MIN_AGE_SECONDS`
- `UA_REMOTE_SYNC_READY_SESSION_PREFIX`

### Current Ready Logic

For gated session types, sync checks the remote workspace and only syncs when the run is terminal and old enough.

Current accepted terminal states in the marker logic include:
- `completed`
- `failed`
- `timed_out`
- `dispatch_failed`
- `failed_pre_dispatch`

Current non-sync reasons include:
- `MISSING`
- `NOT_READY`
- `NOT_TERMINAL`
- `MISSING_COMPLETED`
- `TOO_FRESH`

### Legacy Fallback

If `sync_ready.json` does not exist but `run.log` exists, the script can use that as a legacy readiness signal with age checks.

This preserves compatibility with older workspaces while preferring the explicit marker model.

## 7. Manifest-Based Skip and Optional Deletion

Primary implementation:
- `scripts/sync_remote_workspaces.sh`

The sync loop records synced workspaces in a manifest so it can skip repeats.

Current related features:
- manifest-based skip
- one-session sync mode
- optional artifact sync disable
- optional remote delete after successful sync
- optional prune of old remote workspaces when local copy is missing

Remote deletion is guarded by explicit confirmation-style flags and is not the default behavior.

## 8. Sync Status and Dashboard Interpretation

Primary implementation:
- `src/universal_agent/api/server.py`

The API computes a sync-health view including:
- canonical local workspace root
- canonical local artifacts root
- configured remote roots
- local mirror roots
- counts of mirrored items
- pending ready count
- latest remote/local ready timestamps
- lag seconds
- coarse `sync_state`

Current states used include:
- `in_sync`
- `behind`
- `unknown`

This allows the dashboard to reason about mirror freshness without pretending the mirror is the live execution root.

## 9. Local Fallback Sync Behavior

Primary implementation:
- `src/universal_agent/api/server.py`

Current API-side sync execution has a practical fallback:
- if SSH sync fails because the SSH key does not exist, the server attempts a local-copy fallback sync path instead of only hard-failing

This is convenience behavior for local dev and debugging environments.

## Canonical Environment Controls

Canonical local roots:
- `UA_ARTIFACTS_DIR`

Mirror root controls:
- `UA_VPS_WORKSPACES_MIRROR_DIR`
- `UA_VPS_ARTIFACTS_MIRROR_DIR`

Remote sync controls:
- `UA_REMOTE_SSH_HOST`
- `UA_REMOTE_WORKSPACES_DIR`
- `UA_REMOTE_ARTIFACTS_DIR`
- `UA_LOCAL_MIRROR_DIR`
- `UA_LOCAL_ARTIFACTS_MIRROR_DIR`
- `UA_REMOTE_SYNC_MANIFEST_FILE`
- `UA_REMOTE_SYNC_INCLUDE_ARTIFACTS`
- `UA_REMOTE_SSH_PORT`
- `UA_REMOTE_SSH_KEY`
- `UA_SSH_AUTH_MODE`
- `UA_TAILNET_PREFLIGHT`
- `UA_SKIP_TAILNET_PREFLIGHT`

Ready-marker controls:
- `UA_REMOTE_SYNC_REQUIRE_READY_MARKER`
- `UA_REMOTE_SYNC_READY_MARKER_FILENAME`
- `UA_REMOTE_SYNC_READY_MIN_AGE_SECONDS`
- `UA_REMOTE_SYNC_READY_SESSION_PREFIX`

Remote-toggle controls:
- `UA_REMOTE_GATEWAY_URL`
- `UA_REMOTE_SYNC_TOGGLE_PATH`
- `UA_OPS_TOKEN`

## What Is Actually Implemented Today

### Implemented and Current

- canonical local workspace root under `AGENT_RUN_WORKSPACES`
- canonical durable artifacts root under `UA_ARTIFACTS_DIR` or repo fallback
- explicit local-vs-mirror root selection in storage APIs
- on-demand VPS pull sync from API and scripts
- ready-marker gating for safe mirror sync of terminal runs
- artifact mirror support alongside workspace mirror support

### Important Interpretation Rules

- mirrored VPS storage is for inspection/debugging, not the main source of truth for the active local node
- `local` and `mirror` are deliberate separate roots in the storage API model
- session workspace browsing and durable artifact browsing are different responsibilities and should stay conceptually separate

## Current Gaps and Follow-Up Items

1. **Root defaults are still somewhat split across tools**
   - API server, sync scripts, and older runbooks all agree broadly, but some defaults still reflect older remote host assumptions and path wording

2. **Mirror APIs are operationally useful but easy to misread**
   - operators can confuse mirrored VPS copies with canonical live storage if the `root_source` distinction is not explicit in the UI

3. **Ready-marker gating is selective by prefix**
   - this is pragmatic, but it means not every workspace family is governed by the same terminality rules

4. **Deletion surfaces are powerful**
   - protected suffix rules exist, but deletion workflows should remain explicitly operator-oriented and not silently automated further

## Source Files That Define Current Truth

Primary implementation:
- `src/universal_agent/api/server.py`
- `scripts/sync_remote_workspaces.sh`
- `scripts/pull_remote_workspaces_now.sh`
- `scripts/install_remote_workspace_sync_timer.sh`
- `scripts/remote_workspace_sync_control.sh`
- `web-ui/lib/sessionDirectory.ts`

Relevant supporting docs:
- `docs/03_Operations/22_VPS_Remote_Dev_Deploy_And_File_Transfer_Runbook_2026-02-11.md`
- `docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

## Bottom Line

The canonical current storage model is:
- **local workspaces are the active per-session execution roots**
- **local artifacts are the durable output roots**
- **mirrored VPS storage is a separate debug/inspection layer**
- **remote sync is guarded by ready markers, manifests, and explicit operator controls**

That separation is intentional and should remain explicit in future docs and UI surfaces.
