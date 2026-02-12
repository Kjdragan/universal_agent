# 27. Universal Agent Deployment Runbook (2026-02-12)

## Purpose

This runbook defines the standard procedure for deploying changes to the production VPS.

**Target Environment:**

- **Host**: `187.77.16.29`
- **User**: `root`
- **Directory**: `/opt/universal_agent`
- **Services**: `universal-agent-webui`, `universal-agent-gateway`

## Prerequisite

- SSH access to the VPS via key (`~/.ssh/id_ed25519`).
- Local repository on `dev-telegram` (or main) branch pushed to remote.

## Deployment Method

### Option 1: Automated Script (Recommended)

Run the deployment script from the project root:

```bash
./scripts/deploy_vps.sh
```

This script performs the following:

1. SSH into the VPS.
2. Navigates to the application directory.
3. Pulls the latest code (`git pull`).
4. Syncs Python dependencies (`uv sync`).
5. Restarts the systemd services.
6. Verifies service status.

### Option 2: Manual Deployment

If the script fails or you need granular control:

1. **SSH into the VPS:**

   ```bash
   ssh -i ~/.ssh/id_ed25519 root@187.77.16.29
   ```

2. **Update Code:**

   ```bash
   cd /opt/universal_agent
   git pull
   ```

3. **Update Dependencies:**

   ```bash
   uv sync
   ```

   *(Note: Ensure `uv` is in path, or use `/root/.cargo/bin/uv` if needed)*

4. **Restart Services:**

   ```bash
   systemctl restart universal-agent-webui
   systemctl restart universal-agent-gateway
   ```

5. **Verify Status:**

   ```bash
   systemctl status universal-agent-webui universal-agent-gateway
   ```

## Troubleshooting

- **Git Auth Errors:** Ensure the VPS has a valid deployment key or SSH agent forwarding is enabled.
- **Permission Errors:** Ensure you are running as `root` or have sudo privileges.
- **Service Failures:** Check logs: `journalctl -u universal-agent-gateway -f`.
