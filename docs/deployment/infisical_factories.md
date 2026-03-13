# Infisical Factory Provisioning

This project uses an automated "Factory" pattern to manage secrets. Rather than manual data entry, we use code to provision environments based on predefined roles.

## Provisioning Script

The core logic resides in `scripts/infisical_provision_factory_env.py`. This script performs the following actions:
1.  **Identity Resolution**: Resolves the target machine's slug and project.
2.  **Secret Cloning**: Fetches secrets from a `source` environment (usually `dev`, which acts as the shared template lane).
3.  **Factory Overrides**: Applies role-specific overrides defined in the script (e.g., `HEADQUARTERS`, `LOCAL_WORKER`).
4.  **Upsert**: Creates or updates the target environment in Infisical.

## Usage in CI/CD

During a deployment, the CI/CD pipeline executes the following command on the VPS:

```bash
uv run scripts/infisical_provision_factory_env.py \
  --machine-name "Staging VPS HQ" \
  --machine-slug staging-hq \
  --factory-role HEADQUARTERS \
  --source-env dev
```

Production provisions the existing `prod` environment the same way before restarting services:

```bash
uv run scripts/infisical_provision_factory_env.py \
  --machine-name "Production VPS HQ" \
  --machine-slug prod \
  --factory-role HEADQUARTERS \
  --source-env dev
```

This ensures the production bootstrap `.env` points at `INFISICAL_ENVIRONMENT=prod` instead of continuing to read the shared `dev` lane.

## Naming Guidance

The current Infisical plan supports three environments, so the live lanes are:

- `dev`: shared source/template environment
- `kevins-desktop`: Kevin's local worker environment
- `prod`: production VPS headquarters environment

Within that limit, `prod` is the dedicated production VPS HQ lane. If the environment cap is raised later, the next naming step should be more explicit machine-role-stage slugs, but the runtime contract today is that `prod` is not a generic stage bucket and must not be treated as the shared development lane.

## Adding New Roles

To add a new capability or factory role:
1.  Open `scripts/infisical_provision_factory_env.py`.
2.  Add a new entry to the `FACTORY_OVERRIDES` dictionary.
3.  Define the specific secrets that this role should possess or override.
