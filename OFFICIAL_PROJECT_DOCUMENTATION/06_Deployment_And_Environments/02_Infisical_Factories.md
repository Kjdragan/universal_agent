# Infisical Stage Environments and Factory Bootstrap

Last updated: March 12, 2026

## Purpose

This continuity note explains the current secret/bootstrap model after the
stage-based Infisical refactor.

Canonical deploy details live in:

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`

The living migration record for this refactor lives in:

- `06_Deployment_And_Environments/07_Stage_Based_Infisical_And_Machine_Bootstrap_Migration_Plan_2026-03-12.md`

## Current Model

Infisical environments now represent deployment stage, not machine identity.

Canonical environments:

1. `development`
2. `staging`
3. `production`

Machine identity is provided by local bootstrap and service env files:

- `FACTORY_ROLE`
- `UA_DEPLOYMENT_PROFILE`
- `UA_RUNTIME_STAGE`
- `UA_MACHINE_SLUG`

## Runtime Split

### VPS headquarters nodes

- staging VPS:
  - `INFISICAL_ENVIRONMENT=staging`
  - `UA_RUNTIME_STAGE=staging`
  - `FACTORY_ROLE=HEADQUARTERS`
  - `UA_DEPLOYMENT_PROFILE=vps`

- production VPS:
  - `INFISICAL_ENVIRONMENT=production`
  - `UA_RUNTIME_STAGE=production`
  - `FACTORY_ROLE=HEADQUARTERS`
  - `UA_DEPLOYMENT_PROFILE=vps`

### Kevin desktop lanes

- localhost HQ development:
  - `INFISICAL_ENVIRONMENT=development`
  - `UA_RUNTIME_STAGE=development`
  - `FACTORY_ROLE=HEADQUARTERS`
  - `UA_DEPLOYMENT_PROFILE=local_workstation`
  - checkout: `/home/kjdragan/lrepos/universal_agent`

- deployed-stage local worker:
  - `INFISICAL_ENVIRONMENT=staging` or `production`
  - `UA_RUNTIME_STAGE=staging` or `production`
  - `FACTORY_ROLE=LOCAL_WORKER`
  - `UA_DEPLOYMENT_PROFILE=local_workstation`

## Bootstrap Scripts

### Localhost HQ dev

```bash
bash scripts/bootstrap_local_hq_dev.sh
```

This now always bootstraps the repo checkout as:

- `development`
- `HEADQUARTERS`
- `local_workstation`
- `UA_MACHINE_SLUG=kevins-desktop`

### Desktop local worker

```bash
bash scripts/bootstrap_local_worker_stage.sh --stage staging
```

or:

```bash
bash scripts/bootstrap_local_worker_stage.sh --stage production
```

This keeps the desktop worker stage-switchable without introducing
machine-specific Infisical environments.

## Important Rule

Do not treat `kevins-desktop` or `kevins-desktop-hq-dev` as canonical
Infisical environments anymore. Those names exist only as temporary alias
compatibility during migration.

## Admin Tooling

Stage environment administration now belongs to:

- `scripts/infisical_manage_stage_env.py`

The older helper:

- `scripts/infisical_provision_factory_env.py`

is now legacy and should not be the normal path for stage configuration.
