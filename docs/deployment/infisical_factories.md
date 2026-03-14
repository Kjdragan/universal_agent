# Infisical Stage Environments and Machine Bootstrap

Universal Agent now treats Infisical environments as **stage environments**, not machine environments.

## Canonical Stage Environments

The intended long-term Infisical environments are:

- `development`
- `staging`
- `production`

These environments hold shared stage secrets and integration credentials.

The checked-in local env template is now bootstrap-only:

- `.env.sample`

It should contain only Infisical bootstrap credentials/settings and
machine-local identity. Stage-wide runtime config belongs in Infisical.

## Machine Identity

Machine identity is supplied locally via bootstrap `.env` or service configuration:

- `FACTORY_ROLE`
- `UA_DEPLOYMENT_PROFILE`
- `UA_RUNTIME_STAGE`
- `UA_MACHINE_SLUG`

This allows one stage environment to support:

- VPS headquarters runtime
- desktop local worker runtime
- desktop localhost headquarters development runtime

## Deploy Workflow Contract

Deploy workflows must not provision machine-shaped Infisical environments during normal deploys.

Instead, they:

1. write a minimal bootstrap `.env`
2. select the target stage environment
3. write explicit machine identity values
4. validate secret access against that stage
5. render service env files and restart services

Deploys rewrite that bootstrap file from scratch each time. The checkout `.env`
is treated as bootstrap-only and should not accumulate historical stage-wide or
machine-specific leftovers.

Validation helpers added for this model:

- `scripts/validate_runtime_bootstrap.py`
- `scripts/infisical_manage_stage_env.py`

### Staging VPS bootstrap

- `INFISICAL_ENVIRONMENT=staging`
- `UA_RUNTIME_STAGE=staging`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=vps`
- `UA_MACHINE_SLUG=vps-hq-staging`

### Production VPS bootstrap

- `INFISICAL_ENVIRONMENT=production`
- `UA_RUNTIME_STAGE=production`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=vps`
- `UA_MACHINE_SLUG=vps-hq-production`

## Desktop Bootstrap Modes

### Localhost HQ development

Use [bootstrap_local_hq_dev.sh](/home/kjdragan/lrepos/universal_agent/scripts/bootstrap_local_hq_dev.sh).

This writes:

- `INFISICAL_ENVIRONMENT=development`
- `UA_RUNTIME_STAGE=development`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=local_workstation`
- `UA_MACHINE_SLUG=kevins-desktop`

### Desktop worker for deployed stages

Use [bootstrap_local_worker_stage.sh](/home/kjdragan/lrepos/universal_agent/scripts/bootstrap_local_worker_stage.sh).

This writes:

- `INFISICAL_ENVIRONMENT=staging` or `production`
- `UA_RUNTIME_STAGE=staging` or `production`
- `FACTORY_ROLE=LOCAL_WORKER`
- `UA_DEPLOYMENT_PROFILE=local_workstation`
- `UA_MACHINE_SLUG=kevins-desktop`

## Legacy Compatibility

During migration, the runtime normalizes these old environment aliases:

- `dev` -> `development`
- `prod` -> `production`
- `staging-hq` -> `staging`
- `kevins-desktop-hq-dev` -> `development`

Legacy machine-shaped Infisical environments should be retired after rollout completes.

## CLI Policy

The Infisical CLI and `.infisical.json` are approved for:

- local developer ergonomics
- non-interactive validation
- controlled export for diagnostics

They are not the authoritative deployed runtime selector.

For local CLI convenience, use:

- `.infisical.example.json`

as the template for a local `.infisical.json`.

For local bootstrap convenience, use:

- `.env.sample`

as the bootstrap-only template for `.env`.

## Related Docs

- [architecture_overview.md](/home/kjdragan/lrepos/universal_agent/docs/deployment/architecture_overview.md)
- [ci_cd_pipeline.md](/home/kjdragan/lrepos/universal_agent/docs/deployment/ci_cd_pipeline.md)
- [07_Stage_Based_Infisical_And_Machine_Bootstrap_Migration_Plan_2026-03-12.md](/home/kjdragan/lrepos/universal_agent/docs/06_Deployment_And_Environments/07_Stage_Based_Infisical_And_Machine_Bootstrap_Migration_Plan_2026-03-12.md)
