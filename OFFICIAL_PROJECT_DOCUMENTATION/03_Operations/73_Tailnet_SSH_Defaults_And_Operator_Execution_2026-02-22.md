# 73. Tailnet SSH Defaults And Operator Execution (2026-02-22)

## Summary
This document captures operator execution of the post-canary steady-state workflow:
1. strict deploy run in `tailscale_ssh` mode,
2. persistent shell defaults,
3. confidence status checks,
4. documentation/cleanup closure.

Date: 2026-02-22
Status: COMPLETED

## Preconditions
1. Tailnet Serve policy already enabled.
2. `UA_SSH_AUTH_MODE=tailscale_ssh` canary previously validated (`72`).
3. VPS target: `root@100.106.113.93`.

## Step 1 - Strict Deploy Run
Command:
`UA_VPS_HOST='root@100.106.113.93' UA_SSH_AUTH_MODE=tailscale_ssh UA_TAILNET_STAGING_MODE=required ./scripts/deploy_vps.sh`

Expected PASS criteria:
1. tailnet preflight passed,
2. services active,
3. tailnet staging verify passed,
4. public health `API=200`, `APP=200`,
5. ops auth `OPS_UNAUTH=401`, `OPS_AUTH=200`,
6. deployment completed.

Observed:
1. all PASS criteria satisfied,
2. deploy output ended with `Deployment completed.`.

## Step 2 - Persistent Operator Defaults
Shell profile defaults set:
1. `UA_VPS_HOST='root@100.106.113.93'`
2. `UA_SSH_AUTH_MODE='tailscale_ssh'`
3. `UA_TAILNET_STAGING_MODE='required'`

Applied to: `~/.bashrc` using managed marker block:
1. `# >>> ua_vps_defaults >>>`
2. `# <<< ua_vps_defaults <<<`

## Step 3 - Confidence Checks
Commands:
1. `scripts/vpsctl.sh status all`
2. `scripts/sync_remote_workspaces.sh --status-json`

Expected PASS criteria:
1. core services active,
2. sync status command succeeds in tailscale SSH mode.

Observed:
1. `universal-agent-gateway=active`
2. `universal-agent-api=active`
3. `universal-agent-webui=active`
4. `universal-agent-telegram=active`
5. sync status returned `{\"ok\":true,...}` against `root@srv1360701.taildcc090.ts.net`.

## Step 4 - Cleanup Pass
1. remove stale "pending canary" wording,
2. cross-link completion docs,
3. create one coherent commit for scripts + docs,
4. publish push and rollback commands.

Observed:
1. stale "pending canary" guidance removed in `71` and replaced with canary-validated policy statement linked to `72`.
2. this execution record added as `73`.

## Linkage
1. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/71_Tailnet_First_DevOps_Phases_A_D_Final_Closure_Validation_2026-02-22.md`
2. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/72_Tailnet_SSH_Auth_Mode_Canary_Completion_2026-02-22.md`
