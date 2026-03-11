# Local Factory Setup Guide

Step-by-step guide to deploy a LOCAL_WORKER factory on any machine, starting with Kevin's desktop.

---

## Prerequisites

- **Linux desktop** with Python 3.11+ and [uv](https://github.com/astral-sh/uv)
- **Network access** to HQ VPS Redis (Tailscale recommended, or public IP with UFW rules)
- **Infisical machine identity** for this machine's environment (see Step 1)

## Step 0: Provision Infisical Environment (One-Time)

> Skip if `kevins-desktop` is already provisioned (it is as of 2026-03-06).

```bash
uv run python scripts/infisical_provision_factory_env.py \
  --machine-name "Kevin's Desktop" \
  --machine-slug kevins-desktop \
  --factory-role LOCAL_WORKER
```

See `corporation/docs/INFISICAL_ENVIRONMENTS.md` for the full environment strategy.

## Step 1: Create Infisical Machine Identity (One-Time, Manual)

This step must be done in the Infisical web dashboard — it cannot be automated via API.

1. Navigate to **Infisical → Project → Access Control → Machine Identities**
2. Click **Create Identity**
3. Name it `kevins-desktop` (match the environment slug)
4. Under **Authentication**, select **Universal Auth**
5. Save — copy the generated `Client ID` and `Client Secret`
6. Go to **Project → Members → Machine Identities** tab
7. Add the `kevins-desktop` identity to the project
8. **Scope access to the `kevins-desktop` environment only** (least privilege):
   - Role: **Member** (read-only on secrets)
   - Environment: `kevins-desktop`
9. Store the credentials securely — you'll need them for the deploy script

> **Security:** The machine identity should only have read access to its own environment. It should never see `dev` or `prod` secrets.

## Step 2: Run the Deploy Script

```bash
bash scripts/deploy_local_factory.sh \
  --infisical-client-id <client-id-from-step-1> \
  --infisical-client-secret <client-secret-from-step-1> \
  --infisical-project-id 9970e5b7-d48a-4ed8-a8af-43e923e67572 \
  --infisical-environment kevins-desktop
```

The script will:
1. Clone/update the repo to `~/universal_agent_factory/`
2. Install dependencies via `uv sync`
3. Create a minimal `.env` with **only** Infisical credentials
4. Validate Infisical connectivity (loads secrets successfully)
5. Install a systemd user service
6. Start the factory

### Options

| Flag | Description |
|------|-------------|
| `--factory-dir <path>` | Override factory directory (default: `~/universal_agent_factory`) |
| `--branch <branch>` | Git branch (default: `main`) |
| `--skip-clone` | Use existing code (skip git clone/pull) |
| `--skip-service` | Don't install/start systemd service |

## Step 3: Verify

```bash
# Check service status
systemctl --user status universal-agent-local-factory

# Watch logs
journalctl --user -u universal-agent-local-factory -f

# Verify on HQ (from VPS or with ops token)
curl -H "x-ua-ops-token: <token>" \
  https://api.clearspringcg.com/api/v1/factory/registrations
```

## Architecture

```
HQ (VPS)                          Local Factory (Desktop)
┌──────────────┐                  ┌──────────────────────────────┐
│ Gateway      │                  │ bridge_main.py               │
│              │   Redis Stream   │  ┌─────────────────────┐     │
│ publish ─────┼─────────────────▶│  │ RedisVpBridge       │     │
│ mission      │                  │  │ (consume → SQLite)   │     │
│              │                  │  └──────────┬──────────┘     │
│              │                  │             │ queue_vp_mission│
│              │                  │  ┌──────────▼──────────┐     │
│              │                  │  │ VP SQLite            │     │
│              │                  │  │ vp_missions table    │     │
│              │                  │  └──────────┬──────────┘     │
│              │                  │             │ claim           │
│              │                  │  ┌──────────▼──────────┐     │
│              │                  │  │ VpWorkerLoop         │     │
│              │   Redis Results  │  │ (execute mission)    │     │
│ observe  ◀───┼──────────────────│  └──────────┬──────────┘     │
│ results      │                  │             │ finalize        │
│              │                  │  ┌──────────▼──────────┐     │
│              │                  │  │ RedisVpResultBridge  │     │
│              │                  │  │ (SQLite → publish)   │     │
│              │                  │  └─────────────────────┘     │
└──────────────┘                  └──────────────────────────────┘
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Service won't start | `journalctl --user -u universal-agent-local-factory --no-pager -n 50` |
| Infisical auth failure | Verify machine identity credentials in `.env`; check identity has access to correct environment |
| Redis connection refused | Check `UA_REDIS_HOST`/`UA_REDIS_PORT` in Infisical `kevins-desktop` env; verify Tailscale connectivity |
| Missions not consumed | Verify `UA_DELEGATION_REDIS_ENABLED=1` in Infisical; check bridge logs for consumer group issues |
| Missions consumed but not executed | VP worker loop may not be running; check if `ENABLE_VP_CODER=true` in Infisical |
| Results not published back | Check `RedisVpResultBridge` logs; verify `source=redis_bridge` on missions |

## Updating the Factory

```bash
# Pull latest code and restart
cd ~/universal_agent_factory
git pull origin main
uv sync
systemctl --user restart universal-agent-local-factory
```

## Manual Run (Without systemd)

```bash
cd ~/universal_agent_factory
source .env
PYTHONPATH=src .venv/bin/python -m universal_agent.delegation.bridge_main --verbose
```
