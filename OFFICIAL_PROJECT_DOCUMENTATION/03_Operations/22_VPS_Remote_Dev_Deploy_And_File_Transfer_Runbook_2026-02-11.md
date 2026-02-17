# 22. VPS Remote Dev, Deploy, and File Transfer Runbook (2026-02-11)

## Why this exists
This document is the shared playbook for working with the production VPS from local development.

Goal:
- Make future chats/agents immediately productive.
- Standardize deploy/debug/file-transfer steps.
- Avoid re-learning the same remote workflow every session.

---

## Current environment map
- Local repo path:
  - `/home/kjdragan/lrepos/universal_agent`
- VPS host:
  - `root@100.106.113.93` (Tailscale)
- VPS app root:
  - `/opt/universal_agent`
- Key runtime service units:
  - `universal-agent-gateway`
  - `universal-agent-api`
  - `universal-agent-webui`

Preferred auth:
- SSH key: `~/.ssh/id_ed25519`

---

## Scheduling policy (important)
### Heartbeats
Policy now is:
- No missed-heartbeat backfill.
- No missed-heartbeat alerting/stasis queue items.
- If heartbeat delivery is enabled, next run is the next normal interval.
- If delivery is disabled, it does not run.

Operational interpretation:
- Heartbeats are periodic checks, not debt that must be repaid.
- Missed windows are skipped, not replayed.

---

## Standard deploy flow (local -> VPS)
Run from local machine.

### 1) Copy changed files
```bash
cd /home/kjdragan/lrepos/universal_agent

scp -i ~/.ssh/id_ed25519 \
  src/universal_agent/gateway_server.py \
  root@100.106.113.93:/opt/universal_agent/src/universal_agent/gateway_server.py
```

For multiple files, copy them directly to their exact VPS paths.

### 2) Restart affected service(s)
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  systemctl restart universal-agent-gateway
  systemctl is-active universal-agent-gateway
  systemctl status universal-agent-gateway --no-pager -n 40
'
```

### 3) If UI files changed, rebuild + restart UI
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  set -e
  cd /opt/universal_agent
  npm --prefix web-ui run build
  systemctl restart universal-agent-webui
  systemctl is-active universal-agent-webui
'
```

### 4) If API protocol/auth files changed, restart API
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  systemctl restart universal-agent-api
  systemctl is-active universal-agent-api
  systemctl status universal-agent-api --no-pager -n 40
'
```

---

## Fast debugging commands
### Gateway logs
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  'journalctl -u universal-agent-gateway -n 200 --no-pager'
```

### API logs
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  'journalctl -u universal-agent-api -n 200 --no-pager'
```

### Web UI logs
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  'journalctl -u universal-agent-webui -n 200 --no-pager'
```

---

## File transfer recipes

## Upload a local file into app code on VPS
```bash
scp -i ~/.ssh/id_ed25519 \
  /home/kjdragan/lrepos/universal_agent/path/to/local/file.py \
  root@100.106.113.93:/opt/universal_agent/path/to/remote/file.py
```

## Upload local assets (example: image) to a specific session workspace
```bash
scp -i ~/.ssh/id_ed25519 \
  /home/kjdragan/Pictures/example.png \
  root@100.106.113.93:/opt/universal_agent/AGENT_RUN_WORKSPACES/session_YYYYMMDD_xxxxxxxx/work_products/media/
```

## Download remote logs/workspace files back to local
```bash
scp -i ~/.ssh/id_ed25519 \
  root@100.106.113.93:/opt/universal_agent/AGENT_RUN_WORKSPACES/session_YYYYMMDD_xxxxxxxx/run.log \
  /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_YYYYMMDD_xxxxxxxx/
```

## Sync an entire remote session folder locally
```bash
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" \
  root@100.106.113.93:/opt/universal_agent/AGENT_RUN_WORKSPACES/session_YYYYMMDD_xxxxxxxx/ \
  /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_YYYYMMDD_xxxxxxxx/
```

## One-command manual debug pull (all/new sessions + durable artifacts)
```bash
cd /home/kjdragan/lrepos/universal_agent
scripts/pull_remote_workspaces_now.sh
```

## One-command manual debug pull (single session + durable artifacts)
```bash
cd /home/kjdragan/lrepos/universal_agent
scripts/pull_remote_workspaces_now.sh session_YYYYMMDD_xxxxxxxx
```

Default mirrors created by the sync scripts:
1. `/opt/universal_agent/AGENT_RUN_WORKSPACES` -> `AGENT_RUN_WORKSPACES`
2. `/opt/universal_agent/artifacts` -> `tmp/remote_vps_artifacts`

---

## Recommended future integrations

### 1) `vpsctl` helper script (high value)
Create a local script wrapper for repetitive commands:
- `vpsctl push <local> <remote>`
- `vpsctl restart gateway|api|webui`
- `vpsctl logs gateway|api|webui`
- `vpsctl pull-session <session_id>`

Benefit:
- Lower command mistakes.
- Faster deploy loops.

### 2) Upload endpoint for web-only workflow
Add authenticated ops endpoints for controlled uploads:
- `/api/v1/ops/files/upload` (single file)
- `/api/v1/ops/files/upload-batch` (zip or multipart)

Guardrails:
- Token auth required.
- Path allowlist (session workspace or artifacts only).
- Size limits + content-type checks.

### 3) “Remote file bridge” skill for agents
Create an agent skill that can:
- Push selected local files to VPS.
- Pull selected remote logs/files to local.
- Validate target paths and service restarts.

This would make “work in web UI only” practical while still moving assets/debug files cleanly.

---

## Safety notes
- Never run destructive bulk deletes on VPS without explicit intent.
- Prefer scoped file updates + service restarts.
- Always check service health after deploy.
- Keep auth tokens out of shell history when possible.

---

## Quick checklist for future agents
1. Identify changed files and affected service(s).
2. `scp` files to exact VPS paths.
3. Restart only required unit(s).
4. Verify `systemctl is-active` and recent logs.
5. Ask user to hard refresh and validate in UI.

---

## `vpsctl` helper (local)
For safer/faster repetitive deploy loops, use:
- `scripts/vpsctl.sh push <path...>`
- `scripts/vpsctl.sh restart gateway|api|webui|telegram|all`
- `scripts/vpsctl.sh logs gateway|api|webui|telegram`

This uses only `scp`/`ssh` and preserves the “copy exact files + restart only what changed” workflow.
