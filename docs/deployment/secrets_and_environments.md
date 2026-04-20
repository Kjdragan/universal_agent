# Secrets and Environments — Canonical Guide

> **This is the single entry-point for understanding how Universal Agent manages secrets across all environments.**
> For deep-dive implementation details, see the [Related Docs](#related-docs) section.

## Quick Reference

### Environments

| Environment | Infisical Env | VPS Checkout | Gateway Port | Web UI Port | Web UI URL |
|---|---|---|---|---|---|
| Development | `development` | (desktop) | `8002` | `3000` | `http://localhost:3000` |
| Production | `production` | `/opt/universal_agent` | `8002` | `3000` | `https://app.clearspringcg.com` |

### Key Infisical Commands

```bash
# List all secrets for an environment
infisical secrets --env=development

# Get a single secret value (for scripting)
infisical secrets get UA_OPS_TOKEN --env=production --plain --silent

# Set a secret
infisical secrets set MY_KEY=value --env=production

# Run a command with all secrets injected
infisical run --env=development -- python3 my_script.py
```

## Core Principle

**Infisical is the single source of truth for all application secrets.** Files on disk (`.env`, `.env.local`) contain only:

- **Bootstrap credentials** — the minimum needed to authenticate to Infisical itself
- **Machine identity** — which environment/role/profile this machine is
- **Derived service env** — secrets extracted from Infisical at deploy time for services that can't call Infisical at runtime (e.g., Next.js)

No secret should ever be hardcoded, manually created, or stored in a file without being sourced from Infisical.

## How Secrets Flow

### Python Gateway and API (runtime loading)

```
Process starts
  → reads bootstrap .env (INFISICAL_CLIENT_ID, etc.)
  → calls initialize_runtime_secrets()
  → authenticates to Infisical via SDK (or REST fallback)
  → injects all secrets into os.environ
  → process runs with full secrets available
```

The gateway uses `src/universal_agent/infisical_loader.py` to load secrets at startup. This is the preferred approach — secrets are fetched live, never stored on disk.

### Next.js Web UI (deploy-time rendering)

```
Deploy workflow runs
  → writes bootstrap .env
  → validates Infisical connectivity
  → calls render_service_env_from_infisical.py
  → creates web-ui/.env.local with UA_DASHBOARD_OPS_TOKEN
  → Next.js server reads .env.local at startup
```

Next.js has no Infisical SDK integration, so the deploy workflow extracts the needed secret from Infisical and writes it to `web-ui/.env.local`. This file is:
- Created fresh on every deploy
- Owned by `ua:ua` with permissions `640`
- Never committed to git

## The Two Supported Environments

Both supported environments use the **same pattern**. Only the values differ.

### Bootstrap `.env` Shape (identical key set across all)

```bash
# Infisical authentication
INFISICAL_CLIENT_ID="..."
INFISICAL_CLIENT_SECRET="..."
INFISICAL_PROJECT_ID="9970e5b7-d48a-4ed8-a8af-43e923e67572"

# Stage identity
INFISICAL_ENVIRONMENT="production"    # or "development"
UA_RUNTIME_STAGE="production"
UA_INFISICAL_ENABLED="1"

# Machine identity
FACTORY_ROLE="HEADQUARTERS"           # or "LOCAL_WORKER"
UA_DEPLOYMENT_PROFILE="vps"           # or "local_workstation"
UA_MACHINE_SLUG="vps-hq-production"

# Service ports (VPS only)
UA_GATEWAY_PORT="8002"
UA_API_PORT="8001"
UA_GATEWAY_URL="http://127.0.0.1:8002"
```

### Development (Desktop)

- **Bootstrap script:** `scripts/bootstrap_local_hq_dev.sh`
- **Webui env:** `scripts/install_local_webui_env.sh` → calls `render_service_env_from_infisical.py`
- **Strict mode:** Off by default (allows dotenv fallback)
- **Machine slug:** `kevins-desktop`

### Production (VPS)

- **Bootstrap:** Written by `deploy.yml` on every push to `main`
- **Webui env:** Rendered by `render_service_env_from_infisical.py` during deploy
- **Strict mode:** On (fails closed if Infisical unavailable)
- **Machine slug:** `vps-hq-production`
- **Heartbeat/Cron:** Enabled in Infisical — this is the live system

### X API / Claude Code Intel Secrets

The Claude Code intelligence lane uses X API app-only read access to poll `@ClaudeDevs`.

| Secret | Required for current lane? | Notes |
|---|---:|---|
| `X_BEARER_TOKEN` | Yes | App-only read token used by `src/universal_agent/services/claude_code_intel.py` |
| `CLIENT_ID` / `CLIENT_SECRET` | No | Generic OAuth2 names for official X tooling compatibility |
| `X_OAUTH2_CLIENT_ID` / `X_OAUTH2_CLIENT_SECRET` | No | Namespaced OAuth2 aliases for future user-context flows |
| `X_OAUTH_CONSUMER_SECRET` | No | OAuth1 secret only; incomplete without `X_OAUTH_CONSUMER_KEY` |
| `X_OAUTH_ACCESS_TOKEN` / `X_OAUTH_ACCESS_TOKEN_SECRET` | No | Future OAuth1 user-context support |
| `X_OAUTH_CALLBACK_HOST` / `X_OAUTH_CALLBACK_PORT` / `X_OAUTH_CALLBACK_PATH` | No | Local callback metadata for future OAuth tooling |

Current implementation is read-only and must not use posting endpoints without a separate approval-gated design. See [X API And Claude Code Intel Source Of Truth](../03_Operations/118_X_API_And_Claude_Code_Intel_Source_Of_Truth_2026-04-19.md).

## Deploy Workflow Contract

The single production workflow, `.github/workflows/deploy.yml`, follows this sequence:

1. **Write bootstrap `.env`** — from GitHub Secrets (Infisical machine identity credentials)
2. **Sync dependencies** — `uv sync`
3. **Validate bootstrap** — `scripts/validate_runtime_bootstrap.py` (confirms Infisical connectivity, verifies expected identity values)
4. **Render webui env** — `scripts/render_service_env_from_infisical.py` (fetches `UA_OPS_TOKEN` from Infisical, writes `web-ui/.env.local`)
5. **Build web UI** — `npm install && npm run build`
6. **Restart services** — systemd restart of gateway, API, webui

The deploy creates/overwrites the bootstrap `.env` and `web-ui/.env.local` on every run. These files are transient — they're refreshed from Infisical each deploy.

## Key Scripts

| Script | Purpose |
|---|---|
| `scripts/bootstrap_local_hq_dev.sh` | Desktop: writes `.env`, renders webui env, validates |
| `scripts/bootstrap_local_worker_stage.sh` | Desktop: configures as local worker for deployed-stage worker testing |
| `scripts/install_local_webui_env.sh` | Renders webui `.env.local` from Infisical |
| `scripts/render_service_env_from_infisical.py` | Generic: fetches secrets from Infisical, writes service env files |
| `scripts/validate_runtime_bootstrap.py` | Validates Infisical connectivity and expected identity |
| `scripts/infisical_manage_stage_env.py` | Admin: compare, backup, sync stage environments |
| `scripts/infisical_upsert_secret.py` | Admin: write individual secrets to Infisical |

## Security Model

### Strict Mode

| Profile | Default Strict? | Meaning |
|---|---|---|
| `vps` | Yes | Process fails to start if Infisical is unreachable |
| `standalone_node` | Yes | Same as VPS |
| `local_workstation` | No | Falls back to dotenv if Infisical unavailable |

Override with `UA_INFISICAL_STRICT=0` or `UA_INFISICAL_STRICT=1`.

### File Permissions

All `.env` and `.env.local` files on VPS:
- Owned by `ua:ua`
- Permissions `600` (bootstrap `.env`) or `640` (webui `.env.local`)
- Never committed to git (in `.gitignore`)
- Overwritten fresh on every deploy

### What's NOT in Infisical

Control-plane credentials (e.g., `TAILSCALE_ADMIN_API_TOKEN`) are stored in Infisical under dedicated paths (e.g., `/tailscale` in `production`) and are not injected into the normal app runtime. See [doc 85](../03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md#control-plane-secrets) for details.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Production dashboard shows 401 on ops endpoints | `web-ui/.env.local` missing `UA_DASHBOARD_OPS_TOKEN` | Re-deploy (auto-creates from Infisical) |
| Gateway fails to start on VPS | Infisical unreachable + strict mode | Check Infisical status, verify bootstrap `.env` credentials |
| Local dev can't reach Infisical | CLI token expired | Run `infisical login` again |
| Secrets not updating after Infisical change | Process reads secrets at startup only | Restart the service (`systemctl restart ...`) |

## Related Docs

| Doc | Purpose |
|---|---|
| [infisical_factories.md](infisical_factories.md) | Stage environment naming and machine bootstrap contract (superseded by this doc) |
| [85_Infisical_Secrets_Architecture...](../03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md) | Deep-dive: loader implementation, fetch strategies, failure modes |
| [89_Runtime_Bootstrap...](../03_Operations/89_Runtime_Bootstrap_Deployment_Profiles_And_Factory_Role_Source_Of_Truth_2026-03-06.md) | Deep-dive: deployment profiles, factory roles, runtime policy matrix |
| [97_Infisical_CLI_Reference...](../03_Operations/97_Infisical_CLI_Reference_And_Lessons_Learned_2026-03-14.md) | CLI reference: commands, authentication, lessons learned |
| [architecture_overview.md](architecture_overview.md) | Deployment architecture: git branching, environmental mapping |
| [ci_cd_pipeline.md](ci_cd_pipeline.md) | CI/CD pipeline: workflow details, timing |
