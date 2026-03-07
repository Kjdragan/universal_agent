# Infisical Environment Strategy for Factory Deployments

> **Last updated:** 2026-03-06

Infisical is the **single source of truth** for all runtime parameters across the Universal Agent Corporation. Each factory machine gets its own Infisical environment with machine-specific overrides applied on top of a shared baseline.

## Naming Convention

Environments are named **by machine**, not by role or generic labels. This allows multiple machines with the same factory role to have distinct configurations.

| Slug | Display Name | Factory Role | Status |
|------|-------------|-------------|--------|
| `dev` | Development | `HEADQUARTERS` | **Active** — VPS HQ baseline |
| `kevins-desktop` | Kevin's Desktop | `LOCAL_WORKER` | **Active** — 135 secrets (125 from dev + 15 overrides) |
| `prod` | Production | *(reserved)* | Unused — available for future VPS prod split |
| *(future)* `kevins-tablet` | Kevin's Tablet | `LOCAL_WORKER` | Planned (requires plan upgrade) |
| *(future)* `kevins-phone` | Kevin's Phone | `STANDALONE_NODE` | Planned (requires plan upgrade) |

> **Free Plan Constraint:** Infisical's free tier allows **3 environments** max. The original `staging` environment was repurposed as `kevins-desktop` (renamed via API on 2026-03-06). To add a 4th+ environment, the Infisical plan must be upgraded. The `prod` slot remains available.

### Slug Rules
- Lowercase, hyphen-separated (Infisical requirement)
- Format: `{owner}-{device}` (e.g., `kevins-desktop`, `kevins-tablet`)
- Must be unique within the Infisical project

## How It Works

### Bootstrap Flow

1. Each machine has a minimal `.env` with only Infisical credentials:
   ```
   INFISICAL_CLIENT_ID=<machine identity>
   INFISICAL_CLIENT_SECRET=<machine identity>
   INFISICAL_PROJECT_ID=<shared project>
   INFISICAL_ENVIRONMENT=kevins-desktop
   ```

2. At startup, `infisical_loader.py` → `initialize_runtime_secrets()`:
   - Authenticates with the machine identity
   - Fetches all secrets from the specified environment
   - Injects them into `os.environ` with `overwrite=True`

3. `runtime_role.py` → `build_factory_runtime_policy()` reads `FACTORY_ROLE` from the now-populated environment and constructs the appropriate `FactoryRuntimePolicy`.

### Relationship to `FACTORY_ROLE`

The `FACTORY_ROLE` environment variable (stored in Infisical) determines the runtime behavior:

| Factory Role | Gateway Mode | UI | Telegram | Heartbeat | Delegation |
|---|---|---|---|---|---|
| `HEADQUARTERS` | `full` | Yes | Yes | `global` | `publish_and_listen` |
| `LOCAL_WORKER` | `health_only` | No | No | `local` | `listen_only` |
| `STANDALONE_NODE` | `full` | Yes | Optional | `local` | `disabled` |

## Override Map: What Differs Per Machine

The following keys are overridden from the `dev` baseline when provisioning a new machine environment. All other keys are copied verbatim.

### LOCAL_WORKER Overrides (e.g., `kevins-desktop`)

| Key | Dev (HQ) Value | Override Value | Rationale |
|-----|---------------|----------------|-----------|
| `FACTORY_ROLE` | `HEADQUARTERS` | `LOCAL_WORKER` | Core role assignment |
| `UA_DEPLOYMENT_PROFILE` | `vps` | `local_workstation` | Controls strict mode, fallback behavior |
| `UA_DELEGATION_REDIS_ENABLED` | `1` | `1` | Needs Redis to receive missions from HQ |
| `UA_VP_EXTERNAL_DISPATCH_ENABLED` | `1` | `0` | Local worker doesn't dispatch to other VPs |
| `UA_ENABLE_HEARTBEAT` | `1` | `0` | No proactive heartbeat scheduler on desktop |
| `UA_ENABLE_CRON` | `1` | `0` | No proactive cron jobs on desktop |
| `UA_INFISICAL_STRICT` | *(auto)* | `0` | Graceful fallback if Infisical unreachable |
| `UA_INFISICAL_ALLOW_DOTENV_FALLBACK` | `0` | `1` | Allow local .env fallback for dev |
| `INFISICAL_ENVIRONMENT` | `dev` | `kevins-desktop` | Self-referencing environment slug |
| `ENABLE_VP_CODER` | `true` | `true` | Can still run local CODIE tasks |
| `UA_ENABLE_GWS_CLI` | `1` | `0` | Desktop doesn't need Google Workspace |
| `UA_HOOKS_ENABLED` | `1` | `0` | No inbound webhook hooks |
| `UA_SIGNALS_INGEST_ENABLED` | `1` | `0` | No CSI signal ingest |
| `UA_AGENTMAIL_ENABLED` | `1` | `0` | No AgentMail on local worker |
| `UA_YT_PLAYLIST_WATCHER_ENABLED` | `1` | `0` | No YouTube playlist watcher |
| `UA_ENABLE_GOOGLE_WORKSPACE_EVENTS` | `0` | `0` | No Gmail polling |

### STANDALONE_NODE Overrides

Same as LOCAL_WORKER, except:
- `FACTORY_ROLE` → `STANDALONE_NODE`
- `UA_DEPLOYMENT_PROFILE` → `standalone_node`
- `UA_DELEGATION_REDIS_ENABLED` → `0` (no delegation bus)

## How to Add a New Factory

### Automated (recommended)

```bash
python scripts/infisical_provision_factory_env.py \
    --machine-name "Kevin's Tablet" \
    --machine-slug kevins-tablet \
    --factory-role LOCAL_WORKER
```

Add `--dry-run` to preview changes without modifying Infisical.

Add `--override KEY=VALUE` for machine-specific extra overrides.

### What the script does

1. Authenticates with Infisical using your local machine identity
2. Fetches all secrets from the source environment (`dev` by default)
3. Creates the new environment if it doesn't exist (or detects it already exists)
4. Applies the role-specific override map
5. Bulk-creates all secrets into the new environment (or updates existing ones)
6. Reports a summary of changes

> **Note:** If the free plan environment limit (3) is reached, you must either repurpose an existing environment or upgrade the plan. To repurpose, rename via `PATCH /api/v1/projects/{projectId}/environments/{envId}` then re-run the provisioning script.

### Manual (fallback)

1. Log into [Infisical dashboard](https://app.infisical.com)
2. Navigate to the UA project → Settings → Environments
3. Create a new environment (use machine slug as the slug)
4. Copy all secrets from `dev`
5. Apply overrides from the table above

## How to Update All Environments

When adding a new secret or changing a baseline value:

1. **Add/update the value in `dev`** (the canonical baseline)
2. **Run the provisioning script** for each machine with `--source-env dev`
   - The script is idempotent: existing secrets are updated, new ones created
3. **Restart the factory gateway** on each machine to pick up the changes

## Machine Identity

Each factory machine needs its own Infisical machine identity for authentication. The identity is created in the Infisical dashboard under Project → Access Control → Machine Identities.

- **VPS HQ**: Uses the existing machine identity (already configured)
- **Desktop**: Needs a new machine identity with read access to the `kevins-desktop` environment
- **Future machines**: Create identity per machine, scope to their specific environment

The machine identity credentials (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`) are the only values that go in the local `.env` file — everything else comes from Infisical.

## Related Files

| File | Purpose |
|------|---------|
| `scripts/infisical_provision_factory_env.py` | Automated environment provisioning |
| `src/universal_agent/infisical_loader.py` | Runtime secret loading |
| `src/universal_agent/runtime_role.py` | Factory role → runtime policy |
| `src/universal_agent/runtime_bootstrap.py` | Bootstrap orchestration |
| `src/universal_agent/feature_flags.py` | Feature flag definitions |
| `.env.sample` | Reference template (NOT the source of truth) |
