# Phase 3c: Local Factory Deployment

**Status:** Not Started
**Priority:** High — validates the entire distributed factory model end-to-end
**Depends on:** Phase 3a (consumer), Phase 3b (heartbeat)

---

## Objective

Create a repeatable playbook and automation scripts to stand up a LOCAL_WORKER factory on any machine (starting with the local desktop). The factory must: clone the repo, install deps, configure `.env` via Infisical, register with HQ, and start the generalized mission consumer loop.

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
#   UA_OPS_TOKEN=<token> bash scripts/deploy_local_factory.sh [--factory-id <id>]
#
# Prerequisites:
#   - git, uv, Python 3.11+
#   - Network access to HQ Redis (Tailscale or public with UFW)
#   - REDIS_PASSWORD available (via Infisical or manual .env)
#
# What this does:
#   1. Clones/updates the repo to ~/universal_agent_factory/
#   2. Runs uv sync to install dependencies
#   3. Creates .env from template with LOCAL_WORKER defaults
#   4. Installs systemd user service
#   5. Starts the factory consumer
#   6. Validates registration with HQ

set -euo pipefail

FACTORY_DIR="${UA_FACTORY_DIR:-$HOME/universal_agent_factory}"
REPO_URL="${UA_REPO_URL:-https://github.com/openclaw/universal_agent.git}"
BRANCH="${UA_FACTORY_BRANCH:-main}"
FACTORY_ID="${1:-$(hostname)}"

# ... (full implementation in the actual script)
```

**Key steps:**
1. Clone or `git pull` the repo into `$FACTORY_DIR`
2. `cd $FACTORY_DIR && uv sync`
3. Generate `.env` from `.env.factory.template` with `FACTORY_ROLE=LOCAL_WORKER`
4. If Infisical is configured, pull secrets for the `LOCAL_WORKER` environment
5. Install systemd user service via `scripts/install_local_factory_service.sh`
6. `systemctl --user start universal-agent-local-factory`
7. Verify: poll HQ `GET /api/v1/factory/registrations` until factory appears

### 2. `.env.factory.template`

```env
# Universal Agent Local Factory Configuration
# Copy to .env and fill in values, or use Infisical injection.

FACTORY_ROLE=LOCAL_WORKER
UA_DEPLOYMENT_PROFILE=local_workstation
UA_FACTORY_ID=<factory-hostname-or-uuid>

# VP Coder (enable if this factory should handle coding tasks)
ENABLE_VP_CODER=true

# LLM Provider (optional override for cost management)
# LLM_PROVIDER_OVERRIDE=ZAI

# Redis delegation bus (required for receiving missions)
UA_DELEGATION_REDIS_ENABLED=1
UA_REDIS_HOST=<vps-tailnet-ip-or-public-ip>
UA_REDIS_PORT=6379
UA_REDIS_DB=0
REDIS_PASSWORD=<from-infisical-or-manual>
UA_DELEGATION_STREAM_NAME=ua:missions:delegation
UA_DELEGATION_CONSUMER_GROUP=ua_workers
UA_DELEGATION_DLQ_STREAM=ua:missions:dlq

# HQ connection (for heartbeat registration and ops API calls)
UA_HQ_BASE_URL=https://api.clearspringcg.com
UA_OPS_TOKEN=<short-lived-ops-token-from-hq>

# Infisical (optional — if using centralized secrets)
# INFISICAL_CLIENT_ID=
# INFISICAL_CLIENT_SECRET=
# INFISICAL_PROJECT_ID=
# INFISICAL_ENVIRONMENT=LOCAL_WORKER
```

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
Description=Universal Agent Local Factory Consumer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/universal_agent_factory
ExecStart=%h/universal_agent_factory/.venv/bin/python -m universal_agent.delegation
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
# Env file for secrets
EnvironmentFile=%h/universal_agent_factory/.env

[Install]
WantedBy=default.target
```

### 5. `corporation/docs/LOCAL_FACTORY_SETUP.md`

Operator-facing setup guide:

```markdown
# Local Factory Setup Guide

## Prerequisites
- Linux desktop with Python 3.11+ and uv
- Network access to HQ VPS (Tailscale recommended, or public with UFW rules)
- REDIS_PASSWORD (from Infisical or ask HQ admin)
- UA_OPS_TOKEN (from HQ: POST /auth/ops-token)

## Quick Start
1. Get an ops token from HQ
2. Run the deploy script
3. Verify in Corporation View

## Detailed Steps
...

## Troubleshooting
- Factory not appearing in Corporation View → check Redis connectivity
- Missions not being consumed → check consumer logs: journalctl --user -u universal-agent-local-factory
- Stale heartbeat → check network path to HQ
...
```

## Files to Modify

### `corporation/infrastructure/redis/redis.conf`
- Add comment documenting Tailscale IP range for UFW rules

### `.env.example` / `.env.sample`
- Add `FACTORY_ROLE`, `UA_FACTORY_ID`, and delegation env vars with comments

## Tests to Create

### `tests/delegation/test_factory_deployment.py`

```python
# Test cases (these are integration-level, may need --live flag):
# 1. .env.factory.template contains all required vars
# 2. deploy_local_factory.sh is executable and passes shellcheck
# 3. systemd service file is valid (systemd-analyze verify)
# 4. Consumer starts and registers with mock HQ endpoint
```

## Validation Commands

```bash
# Deploy
UA_OPS_TOKEN=<token> bash scripts/deploy_local_factory.sh

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

- [ ] `scripts/deploy_local_factory.sh` clones, installs, configures, and starts factory
- [ ] Factory consumer starts and registers with HQ
- [ ] Factory appears in Corporation View with correct capabilities
- [ ] Factory heartbeat keeps `last_seen_at` fresh (< 2 min)
- [ ] Factory receives and completes a test tutorial bootstrap delegation
- [ ] `LOCAL_FACTORY_SETUP.md` guide is complete and accurate
- [ ] `.env.factory.template` contains all required variables
- [ ] Systemd service restarts on failure

## Security Checklist

- [ ] Factory `.env` never committed to git (`.gitignore` entry)
- [ ] REDIS_PASSWORD sourced from Infisical or manual secure transfer
- [ ] UA_OPS_TOKEN is short-lived (1hr) — factory must refresh periodically or use Infisical-injected long-lived token
- [ ] Factory runs as unprivileged user (not root)
- [ ] Factory workspace directory is scoped (`~/universal_agent_factory/`)
