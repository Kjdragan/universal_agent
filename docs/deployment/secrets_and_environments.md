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

**Authoritative-overwrite semantics (2026-05-16):** `_inject_environment_values()` defaults to `overwrite=False` for safety (used by the dotenv fallback path), but `initialize_runtime_secrets()` calls it with `overwrite=True` so Infisical values **win over any pre-existing `os.environ` entries** (systemd `Environment=`, bootstrap `.env`, Python module-import side effects). A small whitelist of bootstrap identity keys is preserved via `_BOOTSTRAP_IDENTITY_KEYS` — `FACTORY_ROLE`, `UA_RUNTIME_STAGE`, `UA_MACHINE_SLUG`, `UA_DEPLOYMENT_PROFILE`, `INFISICAL_*`, etc. — so a machine's role/stage cannot be moved by remote config. Operator-flippable feature flags (`UA_ATLAS_DIRECT_DISPATCH_ENABLED`, `UA_CRON_BACKFILL_ON_RESTART`, etc.) flow from Infisical → `os.environ` reliably even when those keys also happen to be set by the systemd unit. Regression test: `tests/unit/test_infisical_loader.py::test_initialize_runtime_secrets_overwrites_preexisting_env_for_non_identity_keys`.

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
| `X_APP_NAME` / `X_API_APP_NAME` | No | X Developer Console app name |
| `X_APP_ID` / `X_API_APP_ID` | No | X Developer Console app ID |
| `X_APP_STATUS` / `X_APP_DESCRIPTION` | No | App metadata for operator visibility |
| `X_APP_PERMISSIONS` / `X_ACCESS_TOKEN_OWNER` | No | OAuth access-token context |
| `CLIENT_ID` / `CLIENT_SECRET` | No | Generic OAuth2 names for official X tooling compatibility |
| `X_OAUTH2_CLIENT_ID` / `X_OAUTH2_CLIENT_SECRET` | No | Namespaced OAuth2 aliases for future user-context flows |
| `X_OAUTH_CONSUMER_KEY` / `X_OAUTH_CONSUMER_SECRET` | No | OAuth1 app credentials for future user-context flows |
| `X_OAUTH_ACCESS_TOKEN` / `X_OAUTH_ACCESS_TOKEN_SECRET` | No | Future OAuth1 user-context support |
| `X_OAUTH_CALLBACK_HOST` / `X_OAUTH_CALLBACK_PORT` / `X_OAUTH_CALLBACK_PATH` | No | Local callback metadata for future OAuth tooling |

Current implementation is read-only and must not use posting endpoints without a separate approval-gated design. See [X API And Claude Code Intel Source Of Truth](../03_Operations/118_X_API_And_Claude_Code_Intel_Source_Of_Truth_2026-04-19.md).

### Anthropic / ZAI Routing Secrets

The 5 keys below route Anthropic SDK calls (and `claude` CLI subprocess invocations spawned from UA Python services) through the ZAI proxy. They are consumed by every UA Python service that calls `initialize_runtime_secrets()` at startup, and by the interactive `zai()` shell wrapper Kevin uses for explicit cheap inference. For the canonical reference on how this routing works, see [Interactive Coding Environment](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md).

| Secret | Required? | Source | Consumers | Notes |
|---|---:|---|---|---|
| `ANTHROPIC_BASE_URL` | Yes | ZAI proxy | UA Python services + `zai()` wrapper | `https://api.z.ai/api/anthropic` |
| `ANTHROPIC_AUTH_TOKEN` | Yes | ZAI proxy | UA Python services + `zai()` wrapper | ZAI account auth token |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Yes | ZAI proxy | Anthropic SDK model resolution | `glm-5-turbo` |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Yes | ZAI proxy | Anthropic SDK model resolution | `glm-5-turbo` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Yes | ZAI proxy | Anthropic SDK model resolution | `glm-5.1` |

Stage in BOTH `production` (VPS) and `development` (desktop) environments. Adding a key:

```bash
PROJECT_ID=9970e5b7-d48a-4ed8-a8af-43e923e67572
python scripts/infisical_upsert_secret.py --env production --project "$PROJECT_ID" \
  --key ANTHROPIC_BASE_URL --value 'https://api.z.ai/api/anthropic'
# (repeat for development env, and for each of the 5 keys)
```

After staging, restart UA services (`sudo systemctl restart 'universal-agent-*.service'`) so `initialize_runtime_secrets()` picks them up on next startup.

### MCP Server Credentials (`.mcp.json` placeholders)

Claude Code spawns MCP servers from `.mcp.json` at the repo root. Some need real
credentials at process start: `AGENTMAIL_API_KEY`, `DISCORD_BOT_TOKEN`,
`HOSTINGER_API_TOKEN`, and any other MCP server's `env.<KEY>` block. **All such
values MUST be `${VAR}` placeholders in `.mcp.json` — never literal strings.**

Why: the file is committed to git. A literal token sat in `.mcp.json:33` (the
Hostinger one) for ~78 days because someone — likely a "fix" suggestion from
Claude Code Doctor — was followed instead of the canonical pattern. See the
2026-05-08 remediation runbook for what that cost in cleanup work.

**The canonical pattern:**

1. **Store the secret in Infisical** (`production` env). Use
   `scripts/infisical_upsert_secret.py --environment production --secret-env <KEY>`
   from a shell that has the value already exported, or use the Infisical UI.
2. **Reference via `${VAR}` placeholder in `.mcp.json`:**
   ```jsonc
   "hostinger-mcp": {
     "command": "npx",
     "args": ["hostinger-api-mcp@latest"],
     "env": {
       "API_TOKEN": "${HOSTINGER_API_TOKEN}"   // ✅ placeholder, not literal
     }
   }
   ```
3. **Document the key in `.env.example`** under the "MCP server credentials"
   block so future operators know it exists. Do NOT paste a real value into
   `.env.example` or `.env`.

**Resolving the placeholders at runtime:**

`.mcp.json` placeholders are substituted by Claude Code from the env of the
parent process that launched `claude`. UA services don't need anything special
here because they call `initialize_runtime_secrets()` at startup, which fetches
every Infisical secret onto `os.environ`. **An interactive `claude` session
launched from a fresh shell does NOT run that bootstrap.** Use the canonical
launcher:

```bash
./scripts/claude_with_mcp_env.sh [claude args…]
```

The launcher (a thin bash wrapper around `scripts/_claude_launcher.py`) sources
`/opt/universal_agent/.env` for Infisical bootstrap creds, runs UA's
`initialize_runtime_secrets()` to inject all secrets onto `os.environ`, then
`os.execvp("claude", …)` so the bootstrapped env is fully inherited by Claude
Code and every MCP child it spawns. This is the SAME code path UA services use
— single source of truth for auth.

For zero-friction operator UX, alias `claude` to the launcher in the shell rc:

```bash
# ~/.bashrc
if [ -x /opt/universal_agent/scripts/claude_with_mcp_env.sh ]; then
    alias claude='/opt/universal_agent/scripts/claude_with_mcp_env.sh'
fi
```

(Already added on the VPS for user `ua` as of 2026-05-08. Mirror to the desktop
shell rc if you launch interactive `claude` sessions there too — adjust
`UA_INSTALL_ROOT` env var to point at the desktop's UA checkout if it isn't at
`/opt/universal_agent`.)

**Important: ANTHROPIC_* exclusion (2026-05-08 hardening).** The launcher
intentionally **excludes** every `ANTHROPIC_*` key from the Infisical inject
step (it passes `exclude_prefixes=("ANTHROPIC_",)` to
`initialize_runtime_secrets`) and also does a defense-in-depth strip after the
bootstrap. This is required because the same Infisical environment holds:

- The 5 ZAI routing vars (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
  `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
  `ANTHROPIC_DEFAULT_OPUS_MODEL`) — used by UA Python services that need
  cheap GLM inference.
- `ANTHROPIC_API_KEY` — used by direct-SDK code paths (`refinement_agent`,
  `gateway_server` vision endpoint, `proactive_signals`, etc.).

Both must reach UA Python services but neither should reach interactive
`claude`: `ANTHROPIC_BASE_URL` would re-route to ZAI/GLM, and
`ANTHROPIC_API_KEY` would override OAuth and yield
`Invalid API key · Fix external API key` when the key isn't for the same
Anthropic Max account. **UA Python services that need these vars call
`initialize_runtime_secrets()` without the `exclude_prefixes` parameter** and
get all secrets normally — so this hardening only applies to the interactive
launcher path. Canonical reference: see § "Related interactive-claude patterns"
in [`docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md#related-interactive-claude-patterns-different-concerns-same-machine).

**Anti-patterns to never repeat:**

| Anti-pattern | Why wrong | What to do instead |
|---|---|---|
| Inline literal token into `.mcp.json` env block | Token leaks into git permanently; rewriting history doesn't fully scrub forks/CI/PR refs (see 2026-05-08 Hostinger remediation doc) | Use `${VAR}` placeholder + Infisical |
| Source `.env` in a shell wrapper to populate the launcher's env | Wrong primitive on the VPS — UA never uses `.env` for app secrets, only for Infisical bootstrap creds | Use `initialize_runtime_secrets()` (the Python SDK path) |
| Wrap `claude` with `infisical run --env=… -- claude` (the CLI) | The Infisical CLI has a separate auth context (`~/.infisical/` interactive session) that doesn't exist headless on the VPS; falls into an interactive login prompt that fails non-tty | Use the Python SDK path via `scripts/claude_with_mcp_env.sh` |
| Ignore "Claude Code Doctor says MCP needs <TOKEN>" by inlining the literal | Doctor is correctly diagnosing that the env var is unset in the parent process; the fix is to populate the env, not to leak the value | Run `claude` via `scripts/claude_with_mcp_env.sh` |
| Background tools auto-resolving `${VAR}` to literals on disk | Some IDE plugins/Doctor variants try to "help" by resolving placeholders. **If you see a `git status` diff against `.mcp.json` that turns `${VAR}` into a literal, REVERT it before commit** — it's a leak in progress | `git checkout -- .mcp.json` before `git add`, then make sure `${VAR}` is preserved |

**Adding a new MCP server that needs credentials:**

1. Add the MCP server entry to `.mcp.json` with `${YOUR_KEY}` placeholder.
2. `python scripts/infisical_upsert_secret.py --environment production --secret-env YOUR_KEY` (after exporting the value in your shell).
3. `python scripts/infisical_upsert_secret.py --environment development --secret-env YOUR_KEY` (so desktop dev tree has it too, if applicable).
4. Document the key in `.env.example` under "MCP server credentials".
5. Verify end-to-end: `./scripts/claude_with_mcp_env.sh` and use the new MCP server. The `🔓 Infisical bootstrap loaded N secret(s)` line should reflect the increment.

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
| Claude Code Doctor: "MCP server X needs token Y" | Parent process env doesn't have `Y` set, so `${Y}` in `.mcp.json` substitutes to empty | Launch via `scripts/claude_with_mcp_env.sh` (or alias `claude` to it) so `initialize_runtime_secrets()` populates `Y` from Infisical before Claude Code spawns the MCP children. **Do NOT inline a literal value into `.mcp.json`.** |
| `git status` shows `.mcp.json` modified with `${VAR}` → literal substitution | Background tool / Doctor / IDE plugin auto-resolved the placeholder | `git checkout -- .mcp.json` immediately. Never commit the substitution. The placeholder is the canonical form. |

## Related Docs

| Doc | Purpose |
|---|---|
| [infisical_factories.md](infisical_factories.md) | Stage environment naming and machine bootstrap contract (superseded by this doc) |
| [85_Infisical_Secrets_Architecture...](../03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md) | Deep-dive: loader implementation, fetch strategies, failure modes |
| [89_Runtime_Bootstrap...](../03_Operations/89_Runtime_Bootstrap_Deployment_Profiles_And_Factory_Role_Source_Of_Truth_2026-03-06.md) | Deep-dive: deployment profiles, factory roles, runtime policy matrix |
| [97_Infisical_CLI_Reference...](../03_Operations/97_Infisical_CLI_Reference_And_Lessons_Learned_2026-03-14.md) | CLI reference: commands, authentication, lessons learned |
| [architecture_overview.md](architecture_overview.md) | Deployment architecture: git branching, environmental mapping |
| [ci_cd_pipeline.md](ci_cd_pipeline.md) | CI/CD pipeline: workflow details, timing |
