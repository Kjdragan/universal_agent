# 87. Tailscale Architecture and Operations Source of Truth (2026-03-06)

## Deployment Status Note

For application deployment, the canonical path is now GitHub Actions branch-driven CI/CD:

1. `develop` -> staging
2. `main` -> production

References in this document to `scripts/deploy_vps.sh` or `scripts/vpsctl.sh` should be read as legacy or break-glass operational tooling, not the primary deployment contract.

## Purpose

This document is the canonical source of truth for current Tailscale usage in Universal Agent.

It explains how Tailscale is used today for VPS access and remote operations, what parts of the tailnet-first workflow are implemented, how `tailscale serve` is used for private staging, how SSH auth mode switching works, and where older host defaults or transitional behavior still remain.

## Executive Summary

Universal Agent currently uses Tailscale as a **private control plane** for remote development and VPS operations.

The implemented Tailscale model has four main pieces:

1. **Tailnet-first VPS addressing** using MagicDNS as the preferred remote host standard
2. **Tailnet preflight checks** inside deploy, control, and sync tooling
3. **Phased SSH auth selection** via `UA_SSH_AUTH_MODE=keys|tailscale_ssh`
4. **Private tailnet-only staging** using `tailscale serve`

This is not a generic networking abstraction inside the Python runtime. It is primarily an **operations and deployment layer** implemented in shell tooling and runbooks.

## Current Canonical Tailscale Role

Tailscale is currently used for:
- private SSH access to the VPS
- remote deploy and control tooling
- workspace and artifact sync from VPS to local development machine
- private staging routes for the UI and API via `tailscale serve`

Tailscale is not currently the public ingress path for the main production app.

Public internet entry points remain separate.

## Canonical Tailnet Role Model

The canonical current node-role split is:
- `tag:vps` = server nodes only
- `tag:operator-workstation` = human-operated admin workstations
- `tag:ci-gha` = GitHub Actions runner identity for deploy automation

The practical consequence is:
- `mint-desktop` should be tagged `tag:operator-workstation`
- `uaonvps` should be tagged `tag:vps`
- operator workstation access to VPS nodes should be granted explicitly through Tailscale SSH policy rather than by tagging a workstation as a VPS

This role model is now tracked in repo-managed files:
- `infrastructure/tailscale/device_roles.json`
- `infrastructure/tailscale/tailnet-policy.hujson`

Live tailnet management helpers are:
- `scripts/tailscale_set_device_tags.py`
- `scripts/tailscale_apply_policy.py`

These scripts are intended to keep the live tailnet aligned with the canonical role map and SSH policy.
Their current control-plane credential source is Infisical path `prod:/tailscale` because the project could not create an additional `infra-admin` environment at the time of implementation.

## Canonical Remote Host Standard

Preferred canonical remote host:
- `root@uaonvps`

This is the MagicDNS standard adopted by the tailnet-first DevOps work.

### Important Current-State Nuance

Not every script has fully converged on the MagicDNS host yet.

Current split:
- `scripts/deploy_vps.sh` defaults to `root@uaonvps`
- `scripts/vpsctl.sh` defaults to `root@uaonvps`
- some remote sync helpers still default to `root@100.106.113.93`

This means the implemented Tailscale story is **mostly standardized**, but host-default cleanup is not fully complete across all helper scripts.

## Core Implemented Tailscale Components

## 1. Tailnet Preflight Checks

Primary implementations:
- `scripts/deploy_vps.sh`
- `scripts/vpsctl.sh`
- `scripts/sync_remote_workspaces.sh`
- `scripts/tailscale_vps_preflight.sh`

Tailnet preflight logic verifies:
- `tailscale` CLI is installed
- `tailscale status` succeeds
- `tailscale ping <host>` succeeds for the target host

Purpose:
- fail fast when the operator is not actually connected to the tailnet
- distinguish tailnet problems from application or deploy problems
- keep remote tooling from failing later with less actionable SSH errors

### Preflight Controls

Primary env controls:
- `UA_TAILNET_PREFLIGHT`
- `UA_SKIP_TAILNET_PREFLIGHT`

Current behavior model:
- `auto` = run preflight for tailnet-like hosts such as MagicDNS or `100.*`
- `force` / `required` = always run preflight
- `off` / `disabled` = skip preflight
- `UA_SKIP_TAILNET_PREFLIGHT=true` = break-glass bypass

### Script Behavior Differences

- `deploy_vps.sh` fails early if required tailnet preflight does not pass
- `vpsctl.sh` fails early for remote control operations when preflight should run
- `sync_remote_workspaces.sh` can skip a sync cycle rather than crash continuously in looping mode
- `tailscale_vps_preflight.sh` provides a standalone explicit preflight helper with richer output and hints

## 2. SSH Auth Mode Switching

Primary implementations:
- `scripts/deploy_vps.sh`
- `scripts/vpsctl.sh`
- `scripts/sync_remote_workspaces.sh`
- `scripts/pull_remote_workspaces_now.sh`
- `scripts/install_remote_workspace_sync_timer.sh`
- `scripts/remote_workspace_sync_control.sh`

Canonical env control:
- `UA_SSH_AUTH_MODE=keys|tailscale_ssh`

### Meaning

- `keys` = use SSH key injection such as `-i ~/.ssh/id_ed25519`
- `tailscale_ssh` = do not inject key args; rely on host-level SSH/Tailscale policy

### Current Intended Posture

The phased rollout design introduced `tailscale_ssh` without forcing immediate cutover.

Current documented safe default in the Phase C implementation record is:
- `UA_SSH_AUTH_MODE=keys`

However, some sync tooling currently defaults to:
- `tailscale_ssh`

So the real current implementation is a **mixed but controlled transition state**:
- the mode switch is fully implemented
- key-based access remains the conservative fallback
- some sync-oriented paths already assume tailnet-first auth by default

## 3. `tailscale serve` Private Staging

Primary implementation:
- `scripts/configure_tailnet_staging.sh`

Deploy integration:
- `scripts/deploy_vps.sh`

Purpose:
- expose private tailnet-only staging routes for integration/debug work before public smoke testing

Default staging model:
- UI HTTPS `443` -> `http://127.0.0.1:3000`
- API HTTPS `8443` -> `http://127.0.0.1:8002`

### Supported Modes

`configure_tailnet_staging.sh` supports:
- `--ensure`
- `--verify-only`
- `--reset`

### Validation Performed by the Script

The staging script verifies:
- `tailscale status` works
- local UI health responds successfully
- local API health responds successfully
- `tailscale serve status` contains the expected proxy targets

### Staging Env Controls

- `UA_TAILNET_STAGING_UI_HTTPS_PORT`
- `UA_TAILNET_STAGING_UI_TARGET`
- `UA_TAILNET_STAGING_API_HTTPS_PORT`
- `UA_TAILNET_STAGING_API_TARGET`
- `UA_TAILNET_STAGING_UI_HEALTH_PATH`
- `UA_TAILNET_STAGING_API_HEALTH_PATH`
- `UA_TAILNET_STAGING_HEALTH_MAX_ATTEMPTS`
- `UA_TAILNET_STAGING_HEALTH_SLEEP_SECONDS`
- `UA_TAILNET_STAGING_MODE`

### Deploy-Time Staging Behavior

`deploy_vps.sh` integrates tailnet staging after service restart.

Current behavior:
- `disabled` = skip staging setup
- `auto` = try to configure staging and warn if it fails
- `required` / `force` / `strict` = fail deploy if staging setup fails

### Important Constraint

`tailscale serve` must be enabled by tailnet policy for the node.

If not enabled, staging setup fails with an explicit policy blocker message.

## 4. Remote Workspace and Artifact Sync over Tailnet

Primary implementations:
- `scripts/sync_remote_workspaces.sh`
- `scripts/pull_remote_workspaces_now.sh`
- `scripts/install_remote_workspace_sync_timer.sh`
- `scripts/remote_workspace_sync_control.sh`

Purpose:
- mirror remote session workspaces and durable artifacts into local directories for debugging and inspection

This is one of the main operational benefits of the current Tailscale setup.

### Current Tailnet-Specific Behavior

- remote sync supports `keys` and `tailscale_ssh`
- sync enforces or checks tailnet preflight before remote access
- sync can be gated by remote ready markers
- sync is intentionally for mirror/debug workflows, not as proof of runtime parity

## Operational Workflow Today

## Primary Current Workflow

1. Connect local machine to Tailscale
2. Reach VPS over tailnet host
3. Run deploy/control tooling through the tracked scripts
4. Optionally configure or verify tailnet-only staging
5. Use public domains only for final smoke/acceptance checks

### Preferred Operator Entry Points

The current preferred operator tools are:
- `scripts/deploy_vps.sh`
- `scripts/vpsctl.sh`
- `scripts/sync_remote_workspaces.sh`
- `scripts/pull_remote_workspaces_now.sh`

This is preferable to ad hoc raw `ssh` because the scripts encode the current tailnet assumptions and safety checks.

## Canonical Environment Controls

Core Tailscale/VPS access env surface includes:
- `UA_VPS_HOST`
- `UA_REMOTE_SSH_HOST`
- `UA_VPS_SSH_KEY`
- `UA_REMOTE_SSH_KEY`
- `UA_SSH_AUTH_MODE`
- `UA_TAILNET_PREFLIGHT`
- `UA_SKIP_TAILNET_PREFLIGHT`

Tailnet staging env surface includes:
- `UA_TAILNET_STAGING_MODE`
- `UA_TAILNET_STAGING_UI_HTTPS_PORT`
- `UA_TAILNET_STAGING_UI_TARGET`
- `UA_TAILNET_STAGING_API_HTTPS_PORT`
- `UA_TAILNET_STAGING_API_TARGET`
- `UA_TAILNET_STAGING_UI_HEALTH_PATH`
- `UA_TAILNET_STAGING_API_HEALTH_PATH`
- `UA_TAILNET_STAGING_HEALTH_MAX_ATTEMPTS`
- `UA_TAILNET_STAGING_HEALTH_SLEEP_SECONDS`

Remote sync env surface strongly related to Tailscale usage includes:
- `UA_REMOTE_SSH_PORT`
- `UA_REMOTE_WORKSPACES_DIR`
- `UA_LOCAL_MIRROR_DIR`
- `UA_REMOTE_ARTIFACTS_DIR`
- `UA_LOCAL_ARTIFACTS_MIRROR_DIR`

## Current Production / Current Implementation Classification

### Implemented and Current

- MagicDNS host standard adopted in core deploy/control scripts
- tailnet preflight checks implemented in core operations scripts
- `UA_SSH_AUTH_MODE` switch implemented across major VPS tooling
- `tailscale serve` staging script implemented and deploy-integrated

### Transitional / Not Fully Unified Yet

- some sync helpers still default to `100.106.113.93` instead of MagicDNS
- auth-mode defaults differ between some tools (`keys` in some docs/paths, `tailscale_ssh` in some sync helpers)
- `.env.sample` is not yet the canonical place for the Tailscale operator env surface

### Planned / Addendum / Not the Main Deployed Baseline

The tailnet-first planning document also includes a residential tailnet transcript-worker addendum.

This is a useful pattern, but it should be treated as an operational addendum rather than the core baseline Tailscale story unless and until it becomes the standard default production path.

## Health Signals and Failure Signatures

Healthy indicators:
- `tailscale status` succeeds locally or on the relevant node
- `tailscale ping <host>` succeeds
- deploy/control scripts can reach the VPS via the configured host
- `tailscale serve status` shows expected UI/API proxies when staging is enabled

Common failure signatures:
- missing `tailscale` CLI
- `tailscale status` failing because the node is disconnected
- `tailscale ping` failure to the MagicDNS host or tailnet IP
- staging failure because `tailscale serve` is not enabled on the tailnet
- interactive Tailscale SSH approval requirement during first or additional checks
- `tailscale ping` succeeding while SSH fails with `tailnet policy does not permit you to SSH to this node`

When `tailscale ping` succeeds but SSH is denied, treat the issue as a Tailscale ACL/SSH policy problem first.
The canonical remediation order is:
1. verify node tags match `infrastructure/tailscale/device_roles.json`
2. apply the repo-managed policy overlay from `infrastructure/tailscale/tailnet-policy.hujson`
3. only use public-IP allowlisting in the VPS host firewall as a fallback path

## Security and Operations Posture

The current Tailscale posture is:
- prefer private tailnet access for operator workflows
- use public domains only for final smoke where appropriate
- keep break-glass bypass available for urgent cases
- keep SSH auth mode switch explicit instead of scattering custom SSH behavior per script
- keep staging private by default via tailnet routing instead of exposing additional public surfaces
- keep Hostinger-specific public firewall handling out of normal remediation when tailnet access can be fixed directly

## Current Gaps and Follow-Up Items

1. **Host default drift**
   - finish converging helper scripts from `100.106.113.93` to MagicDNS where appropriate

2. **Auth default drift**
   - decide whether the long-term default should remain `keys` or move fully to `tailscale_ssh`
   - then align helper defaults and docs consistently

3. **Central env documentation**
   - Tailscale-related operator env vars are spread across scripts rather than centralized in one env reference file

4. **Runbook convergence**
   - older runbooks still contain direct `100.x` SSH examples and should eventually defer more explicitly to the canonical tailnet-first workflow

5. **Automation bootstrap**
   - the live policy scripts require a Tailscale admin API token stored centrally in Infisical and should not depend on one workstation being the source of truth

## Source Files That Define Current Truth

Primary implementation:
- `scripts/deploy_vps.sh`
- `scripts/vpsctl.sh`
- `scripts/sync_remote_workspaces.sh`
- `scripts/pull_remote_workspaces_now.sh`
- `scripts/configure_tailnet_staging.sh`
- `scripts/tailscale_vps_preflight.sh`
- `scripts/install_remote_workspace_sync_timer.sh`
- `scripts/remote_workspace_sync_control.sh`
- `scripts/tailscale_set_device_tags.py`
- `scripts/tailscale_apply_policy.py`
- `infrastructure/tailscale/device_roles.json`
- `infrastructure/tailscale/tailnet-policy.hujson`

Primary operational records:
- `docs/03_Operations/27_Deployment_Runbook_2026-02-12.md`
- `docs/03_Operations/63_Tailnet_First_DevOps_Profile_And_Staging_Workflow_2026-02-21.md`
- `docs/03_Operations/66_Tailnet_First_DevOps_Phase_A_Implementation_2026-02-22.md`
- `docs/03_Operations/67_Tailnet_First_DevOps_Phase_B_Implementation_2026-02-22.md`
- `docs/03_Operations/68_Tailnet_First_DevOps_Phase_C_SSH_Auth_Mode_Implementation_2026-02-22.md`
- `docs/03_Operations/22_VPS_Remote_Dev_Deploy_And_File_Transfer_Runbook_2026-02-11.md`

## Bottom Line

The canonical current Tailscale implementation in Universal Agent is:
- **tailnet-first private VPS access**
- **preflight-enforced deploy/control/sync workflows**
- **explicit auth mode selection via `keys` or `tailscale_ssh`**
- **private staging through `tailscale serve`**
- **a mostly-converged but not yet perfectly unified host/auth default story across all helper scripts**
