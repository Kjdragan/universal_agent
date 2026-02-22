# 68. Tailnet-First DevOps Phase C SSH Auth Mode Implementation (2026-02-22)

## Summary
This document records Phase C implementation from the Tailnet-first DevOps plan (`63`): introducing a deterministic SSH auth mode switch for operations tooling.

Date: 2026-02-22  
Status: Implemented (Phase C code scope)

## Goal
Introduce a unified auth mode control:
- `UA_SSH_AUTH_MODE=keys|tailscale_ssh`

Design intent:
1. Keep `keys` as default for compatibility.
2. Allow canary/transition to `tailscale_ssh` without rewriting scripts.
3. Preserve break-glass flexibility while reducing per-script auth drift.

## Files Updated
1. `scripts/deploy_vps.sh`
2. `scripts/vpsctl.sh`
3. `scripts/sync_remote_workspaces.sh`
4. `scripts/pull_remote_workspaces_now.sh`
5. `scripts/install_remote_workspace_sync_timer.sh`
6. `scripts/remote_workspace_sync_control.sh`

## Implemented Behavior

### 1. Mode normalization and validation
All updated scripts normalize and validate mode to one of:
1. `keys`
2. `tailscale_ssh`

Invalid mode fails fast with explicit error.

### 2. Key mode (`keys`)
1. Existing SSH-key flow is preserved.
2. Scripts that require local key file validate presence/path when applicable.
3. Default remains key-based (`keys`) to avoid breaking current operators.

### 3. Tailscale SSH mode (`tailscale_ssh`)
1. Scripts skip `-i <key>` injection and rely on host-level SSH/Tailscale policy.
2. Wrapper scripts stop passing `--ssh-key` into downstream commands.
3. This enables auth-mode canary without replacing remote host routing/logic.

## Notable Script-Specific Changes

### `scripts/deploy_vps.sh`
1. Adds `UA_SSH_AUTH_MODE` handling.
2. Builds SSH/rsync transport args dynamically from auth mode.
3. Connectivity probe now adapts to selected auth mode.

### `scripts/vpsctl.sh`
1. Adds `UA_SSH_AUTH_MODE` support for both `ssh` and `scp` paths.
2. Key existence check applies only in `keys` mode.

### `scripts/sync_remote_workspaces.sh`
1. Adds `--ssh-auth-mode <keys|tailscale_ssh>` CLI option.
2. Adds env default `UA_SSH_AUTH_MODE`.
3. In `tailscale_ssh`, suppresses key injection into SSH transport.

### `scripts/pull_remote_workspaces_now.sh`
1. Passes auth mode through to `sync_remote_workspaces.sh`.
2. Adds key arg only when mode is `keys`.

### `scripts/install_remote_workspace_sync_timer.sh`
1. Adds `--ssh-auth-mode` option and env-backed default.
2. Persists auth mode into generated timer ExecStart.
3. Includes key only when mode is `keys`.

### `scripts/remote_workspace_sync_control.sh`
1. Adds `--ssh-auth-mode` option and env-backed default.
2. Propagates auth mode to both timer install path and `sync-now` path.
3. Includes key only when mode is `keys`.

## Validation Performed
1. Syntax checks:
- `bash -n scripts/deploy_vps.sh scripts/vpsctl.sh scripts/sync_remote_workspaces.sh scripts/pull_remote_workspaces_now.sh scripts/remote_workspace_sync_control.sh scripts/install_remote_workspace_sync_timer.sh`
2. Help/smoke checks:
- `scripts/vpsctl.sh --help`
- `scripts/sync_remote_workspaces.sh --help`
- `scripts/install_remote_workspace_sync_timer.sh --help`
- `scripts/remote_workspace_sync_control.sh --help`
3. Mode smoke check:
- `UA_SSH_AUTH_MODE=tailscale_ssh UA_SKIP_TAILNET_PREFLIGHT=true scripts/pull_remote_workspaces_now.sh --status-json`

Observed in local environment:
- Hostname resolution for `srv1360701.taildcc090.ts.net` is currently unavailable here; this is network/runtime context, not parser/syntax failure.

## Operational Guidance
1. Current safe default remains:
- `UA_SSH_AUTH_MODE=keys`
2. Canary path:
- set `UA_SSH_AUTH_MODE=tailscale_ssh` in controlled scope
- validate deploy/sync/status command behavior
- keep key-mode rollback available

## Linkage
1. Plan source: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/63_Tailnet_First_DevOps_Profile_And_Staging_Workflow_2026-02-21.md`
2. Phase A: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/66_Tailnet_First_DevOps_Phase_A_Implementation_2026-02-22.md`
3. Phase B: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/67_Tailnet_First_DevOps_Phase_B_Implementation_2026-02-22.md`
