# Phase 3c: Local Factory Deployment (Infisical-First)

**Status:** In Progress (updated 2026-03-06)
**Priority:** High — validates the entire distributed factory model end-to-end
**Depends on:** Phase 3a-bridge (Redis→SQLite bridge), VP worker system (complete — Track B)

---

## What Changed (2026-03-06)

- **Infisical is the canonical parameter store** — `.env.factory.template` is removed from this spec
- **Environments are named by machine** (e.g., `kevins-desktop`), not by role
- **Provisioning is automated** via `scripts/infisical_provision_factory_env.py`
- **The consumer is the VP worker system** (not a new consumer built from scratch) — Phase 3a-bridge bridges Redis → VP SQLite
- **Infisical `kevins-desktop` environment** has been provisioned (3c.0 complete)

See `corporation/docs/INFISICAL_ENVIRONMENTS.md` for the full environment strategy.

## Objective

Create a repeatable playbook and automation scripts to stand up a LOCAL_WORKER factory on any machine (starting with Kevin's desktop). The factory must: clone the repo, install deps, configure a minimal `.env` with Infisical credentials only (all other params come from Infisical), and start the Redis→SQLite bridge + VP worker loop.

## Current Desktop Environment

- Machine: `kjdragan` local desktop (Linux)
- Python: managed via `uv`
- Network: Tailscale mesh to VPS (preferred) or public IP with UFW rules
- Target: run as a systemd user service for persistence

## Files to Create

### 1. `scripts/deploy_local_factory.sh`

```bash
#!/usr/bin/env bash
# Deploy a LOCAL_WORKER factory on the current machine.
#
# Usage:
#   bash scripts/deploy_local_factory.sh \
#     --infisical-client-id <id> \
#     --infisical-client-secret <secret> \
#     --infisical-project-id <project> \
#     --infisical-environment kevins-desktop
#
# Prerequisites:
#   - git, uv, Python 3.11+
#   - Network access to HQ Redis (Tailscale or public with UFW)
#   - Infisical machine identity for this machine's environment
#
# What this does:
#   1. Clones/updates the repo to ~/universal_agent_factory/
#   2. Runs uv sync to install dependencies
#   3. Creates minimal .env with ONLY Infisical credentials
#   4. Validates Infisical connectivity (secrets load successfully)
#   5. Installs systemd user services (gateway + bridge + VP workers)
#   6. Starts the factory
#   7. Validates registration with HQ

set -euo pipefail

FACTORY_DIR="${UA_FACTORY_DIR:-$HOME/universal_agent_factory}"
REPO_URL="${UA_REPO_URL:-https://github.com/openclaw/universal_agent.git}"
BRANCH="${UA_FACTORY_BRANCH:-main}"

# ... (full implementation in the actual script)
```

**Key steps:**
1. Clone or `git pull` the repo into `$FACTORY_DIR`
2. `cd $FACTORY_DIR && uv sync`
3. Create minimal `.env` with Infisical credentials only (4 keys)
4. Validate: `python -c "from universal_agent.infisical_loader import ...; ..."` succeeds
5. Install systemd user services
6. `systemctl --user start universal-agent-local-factory`
7. Verify: poll HQ `GET /api/v1/factory/registrations` until factory appears

### 2. Minimal `.env` (Infisical credentials only)

The factory `.env` file contains **only** Infisical machine identity credentials. All other parameters (FACTORY_ROLE, Redis config, API keys, feature flags) are loaded from Infisical at startup.

```env
# Universal Agent Local Factory — Infisical Bootstrap
# All runtime parameters are loaded from Infisical at startup.
# This file contains ONLY the credentials needed to authenticate.
INFISICAL_CLIENT_ID=<machine-identity-from-infisical>
INFISICAL_CLIENT_SECRET=<machine-identity-from-infisical>
INFISICAL_PROJECT_ID=<shared-project-id>
INFISICAL_ENVIRONMENT=kevins-desktop
```

> **Note:** There is no `.env.factory.template` file. Use `scripts/infisical_provision_factory_env.py` to create the Infisical environment, then create this minimal `.env` with the machine identity credentials.

### 3. `scripts/install_local_factory_service.sh`

```bash
#!/usr/bin/env bash
# Install systemd user service for the local factory consumer.
set -euo pipefail

FACTORY_DIR="${UA_FACTORY_DIR:-$HOME/universal_agent_factory}"
SERVICE_NAME="universal-agent-local-factory"

# Copy service file
mkdir -p ~/.config/systemd/user
cp "$FACTORY_DIR/deployment/systemd-user/${SERVICE_NAME}.service" \
   ~/.config/systemd/user/

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
echo "Installed $SERVICE_NAME. Start with: systemctl --user start $SERVICE_NAME"
```

### 4. `deployment/systemd-user/universal-agent-local-factory.service`

```ini
[Unit]
Description=Universal Agent Local Factory (Gateway + Bridge + VP Workers)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/universal_agent_factory
ExecStart=%h/universal_agent_factory/.venv/bin/python -m universal_agent.gateway_server
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
# Minimal .env with Infisical credentials only
EnvironmentFile=%h/universal_agent_factory/.env

[Install]
WantedBy=default.target
```

> **Note:** The gateway server starts the Redis→SQLite bridge and VP worker loops as background tasks when `FACTORY_ROLE=LOCAL_WORKER`. No separate consumer process needed.

### 5. `corporation/docs/LOCAL_FACTORY_SETUP.md`

Operator-facing setup guide:

```markdown
# Local Factory Setup Guide

## Prerequisites
- Linux desktop with Python 3.11+ and uv
- Network access to HQ VPS (Tailscale recommended, or public with UFW rules)
- Infisical machine identity for your machine's environment

## Quick Start
1. Provision Infisical environment (if not already done):
   python scripts/infisical_provision_factory_env.py \
     --machine-name "Kevin's Desktop" --machine-slug kevins-desktop --factory-role LOCAL_WORKER
2. Create machine identity in Infisical dashboard (scoped to kevins-desktop env)
3. Run the deploy script with Infisical credentials
4. Verify in Corporation View

## Troubleshooting
- Factory not appearing in Corporation View → check Redis connectivity
- Missions not being consumed → check bridge logs: journalctl --user -u universal-agent-local-factory
- Infisical auth failure → verify machine identity credentials in .env
...
```

## Files to Modify

### `corporation/infrastructure/redis/redis.conf`
- Add comment documenting Tailscale IP range for UFW rules

### `src/universal_agent/gateway_server.py`
- When `FACTORY_ROLE=LOCAL_WORKER`: start Redis→SQLite bridge + VP worker loops as background tasks

## Tests to Create

### `tests/delegation/test_factory_deployment.py`

```python
# Test cases (these are integration-level, may need --live flag):
# 1. deploy_local_factory.sh is executable and passes shellcheck
# 2. systemd service file is valid (systemd-analyze verify)
# 3. Factory starts with Infisical kevins-desktop environment and registers with mock HQ
# 4. Infisical provisioning script creates correct override keys
```

## Validation Commands

```bash
# Provision Infisical environment (one-time)
python scripts/infisical_provision_factory_env.py \
  --machine-name "Kevin's Desktop" --machine-slug kevins-desktop --factory-role LOCAL_WORKER

# Deploy
bash scripts/deploy_local_factory.sh \
  --infisical-client-id <id> --infisical-client-secret <secret> \
  --infisical-project-id <project> --infisical-environment kevins-desktop

# Check service status
systemctl --user status universal-agent-local-factory

# Check logs
journalctl --user -u universal-agent-local-factory -f

# Verify registration on HQ
curl -H "x-ua-ops-token: <token>" https://api.clearspringcg.com/api/v1/factory/registrations

# Verify in Corporation View UI
# → Navigate to /dashboard/corporation → factory should appear with "online" status
```

## Acceptance Criteria

- [x] Infisical `kevins-desktop` environment provisioned via `scripts/infisical_provision_factory_env.py`
- [x] `scripts/deploy_local_factory.sh` clones, installs, creates minimal .env, starts factory
- [ ] Factory starts with Infisical environment and loads all parameters (needs machine identity)
- [ ] Factory appears in Corporation View with correct capabilities (needs live test)
- [ ] Factory receives and completes a test delegation via Redis→SQLite bridge (needs live test)
- [x] `LOCAL_FACTORY_SETUP.md` guide is complete and accurate
- [x] Systemd service restarts on failure (`Restart=on-failure` in service file)

## Security Checklist

- [x] Factory `.env` contains only Infisical credentials (never API keys or secrets directly)
- [x] Factory `.env` never committed to git (`.gitignore` entry — deploy script sets mode 600)
- [ ] Infisical machine identity scoped to `kevins-desktop` environment only (needs dashboard creation)
- [x] All secrets (REDIS_PASSWORD, API keys, etc.) loaded from Infisical at runtime
- [x] Factory runs as unprivileged user (systemd user service, not root)
- [x] Factory workspace directory is scoped (`~/universal_agent_factory/`)
