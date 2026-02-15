# 27. Universal Agent Deployment Runbook (2026-02-14)

## Purpose

This runbook defines the standard production deployment path for the VPS.

## Prerequisites

- **Tailscale Active**: You must be connected to the Tailscale VPN to access the VPS SSH port.
- **SSH Key**: `~/.ssh/id_ed25519` must be authorized on `root@100.106.113.93`.

Target:

1. Host: `root@100.106.113.93` (Tailscale Secure Mesh)
2. App dir: `/opt/universal_agent`
3. Services:
   1. `universal-agent-gateway`
   2. `universal-agent-api`
   3. `universal-agent-webui`
   4. `universal-agent-telegram`

## Important Reality

The VPS app directory is not guaranteed to be a git clone.

Do not assume:

1. `git pull` works on VPS.
2. VPS branch state matches local branch state.

Deployment must be file-sync based from local workspace.

## Recommended Workflow

1. Commit local changes.
2. Push branch to remote (for source-of-truth history).
3. Deploy local `HEAD` to VPS via `scripts/deploy_vps.sh`.
4. Validate services and public endpoints.

Alternative (small incremental deploys):

- If you only need to push a few files and restart one service, use the `scp`-based flow in:
  - `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/22_VPS_Remote_Dev_Deploy_And_File_Transfer_Runbook_2026-02-11.md`
- Helper wrapper:
  - `scripts/vpsctl.sh`

## Standard Command

Run from local project root:

```bash
./scripts/deploy_vps.sh
```

What this script does:

1. `rsync` local workspace to `/opt/universal_agent` (excluding runtime state/secrets).
2. Preserves `.env` on VPS.
3. Reapplies secure `.env` mode (`root:ua`, `640`).
4. Runs `uv sync` as user `ua` when available.
5. Runs `npm install && npm run build` for `web-ui` as user `ua`.
6. Restarts all core services.
7. Verifies service health and public endpoint responses.
8. Verifies ops auth gate (`401` unauth, `200` auth) when token is present.

## Why this method

1. Works even when `/opt/universal_agent` is not a git repository.
2. Prevents accidental `.env` overwrite.
3. Keeps ownership consistent so builds do not fail with `EACCES`.
4. Produces deterministic deploy behavior from your local tested state.

## Failure Handling

If deploy fails:

1. Check build errors first:
   1. `journalctl -u universal-agent-webui -n 200 --no-pager`
2. Check gateway/api status:
   1. `journalctl -u universal-agent-gateway -n 200 --no-pager`
   2. `journalctl -u universal-agent-api -n 200 --no-pager`
3. Re-run deploy after fixing source errors locally.

## Security Guardrails

1. Never overwrite VPS `.env` blindly from local.
2. Keep deployment profile in VPS mode:
   1. `UA_DEPLOYMENT_PROFILE=vps`
3. Ensure ops/internal tokens remain present in VPS `.env`.
4. Keep SSH key-based access and host hardening controls active.

## Quick Post-Deploy Check

```bash
ssh -i ~/.ssh/id_ed25519 root@187.77.16.29 '
for s in universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram; do
  printf "%s=" "$s"; systemctl is-active "$s"
done
curl -s https://api.clearspringcg.com/api/v1/health | head -c 220; echo
'
```
