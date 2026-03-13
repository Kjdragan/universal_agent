# Stage-Based Infisical and Machine Bootstrap Migration Plan

Last updated: 2026-03-12

## Purpose

This document is the official migration record for moving Universal Agent from
machine-shaped Infisical environments to a stage-based secret model with
machine-local bootstrap identity.

## Current State

- historical Infisical usage mixed stage and machine identity
- older docs referenced environments such as `kevins-desktop` and `staging-hq`
- deploy workflows previously provisioned stage-like environments during deploy
- runtime identity was partly inferred from environment slug instead of explicit
  bootstrap values

## Target Model

Infisical environments represent stage only:

1. `development`
2. `staging`
3. `production`

Machine identity comes from local bootstrap:

- `FACTORY_ROLE`
- `UA_DEPLOYMENT_PROFILE`
- `UA_RUNTIME_STAGE`
- `UA_MACHINE_SLUG`

## Stage Environments

### development

Used for localhost development and non-deployed local HQ workflows.

### staging

Used by the staging VPS headquarters runtime and optionally by the desktop
local worker when attached to staging.

### production

Used by the production VPS headquarters runtime and optionally by the desktop
local worker when attached to production.

## Machine Bootstrap Identity

### Kevin desktop, localhost HQ development

- `INFISICAL_ENVIRONMENT=development`
- `UA_RUNTIME_STAGE=development`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=local_workstation`
- `UA_MACHINE_SLUG=kevins-desktop`

### Kevin desktop, deployed local worker

- `INFISICAL_ENVIRONMENT=staging` or `production`
- `UA_RUNTIME_STAGE=staging` or `production`
- `FACTORY_ROLE=LOCAL_WORKER`
- `UA_DEPLOYMENT_PROFILE=local_workstation`
- `UA_MACHINE_SLUG=kevins-desktop`

### staging VPS headquarters

- `INFISICAL_ENVIRONMENT=staging`
- `UA_RUNTIME_STAGE=staging`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=vps`
- `UA_MACHINE_SLUG=vps-hq-staging`

### production VPS headquarters

- `INFISICAL_ENVIRONMENT=production`
- `UA_RUNTIME_STAGE=production`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=vps`
- `UA_MACHINE_SLUG=vps-hq-production`

## CI/CD Contract

- `develop` deploys to staging
- `main` deploys to production
- deploy workflows write explicit bootstrap `.env` values
- deploy workflows validate stage access and runtime identity
- deploy workflows must not provision machine-shaped Infisical environments as
  part of normal deployment

## GitHub App Connection Governance

- approved GitHub connection type: Infisical GitHub App
- ownership: centralized admin/platform lane
- scope: approved CI/CD and governance workflows only
- machine bootstrap identity must not be derived from GitHub integration state

## Redis Connection Governance

- Redis access is stage-scoped unless infrastructure truly differs by machine
- Redis App Connections should be centrally managed in Infisical
- use a dedicated Redis user for Infisical-managed operations
- grant ACL mutation permissions only where rotation workflows require them

## Infisical CLI and `.infisical.json` Policy

- Infisical CLI is approved for local development ergonomics, validation, and
  admin/backup workflows
- `.infisical.json` is local convenience only
- deployed services and CI/CD must not depend on `.infisical.json` to resolve
  runtime stage

Recommended local mapping:

- `defaultEnvironment=development`
- `develop -> staging`
- `main -> production`

## Migration Phases

### Phase 0. Backup and inventory

- export backups of existing environments
- inventory bootstrap files and machine identities
- freeze non-essential secret changes during migration

### Phase 1. Rename environments

- rename `dev` -> `development`
- rename `prod` -> `production`
- rename `kevins-desktop` -> `staging`
- clean `staging` so it contains stage secrets, not machine identity

### Phase 2. Implement code and script changes

- normalize legacy aliases in the loader
- add explicit runtime stage and machine slug helpers
- add stage-aware bootstrap scripts
- add runtime bootstrap validation tooling
- update canonical docs and official continuity docs

### Phase 3. Staging rollout

- deploy staging with explicit stage bootstrap
- verify staging VPS runtime identity
- switch desktop worker to `staging` when needed

### Phase 4. Production rollout

- deploy production with explicit stage bootstrap
- verify production VPS runtime identity
- switch desktop worker to `production` when needed

### Phase 5. Remove compatibility aliases

- remove temporary alias support after soak
- remove legacy machine-env references from docs and scripts
- reject old env names hard

## Open Risks

1. some older operational docs still mention historical machine-shaped env names
2. operators may confuse `.infisical.json` local convenience with deployed
   runtime identity
3. legacy admin helpers may still be used incorrectly unless called out as
   legacy
4. production-stage secret ownership still requires disciplined centralized
   review

## Rollout Checklist

- [x] loader supports stage normalization and explicit runtime identity
- [x] deploy workflows write explicit bootstrap identity
- [x] bootstrap validation script added
- [x] local HQ dev bootstrap updated
- [x] local worker stage bootstrap script added
- [x] canonical `docs/deployment/` docs updated
- [x] official migration record created
- [x] `.env.sample` reduced to bootstrap-only
- [x] stage environments renamed in Infisical
- [x] `staging` normalized from `development` and pruned of worker-only leftovers
- [ ] staging lane re-bootstrap verified end to end
- [ ] production lane re-bootstrap verified end to end
- [ ] legacy alias compatibility removed

## Change Log / Implementation Status

- Status: in_progress
- Last updated: 2026-03-12

### Completed steps

- implemented runtime helpers for `UA_RUNTIME_STAGE` and `UA_MACHINE_SLUG`
- updated Infisical loader to normalize legacy env aliases and preserve local
  bootstrap identity over fetched secrets
- updated staging and production deploy workflows to write explicit stage
  bootstrap values and validate runtime identity
- updated local HQ development bootstrap
- added desktop local worker stage bootstrap script
- added admin helper for stage env backup/compare/verify/sync
- reduced `.env.sample` to bootstrap-only and moved stage-wide config expectation into Infisical/docs
- updated canonical deployment docs
- updated official continuity docs to point to the new model
- renamed live Infisical environments to `development`, `staging`, and `production`
- cleaned the live `staging` environment so it now matches `development` instead of the historical desktop-worker lane

### Remaining steps

- rename and clean the live Infisical environments
- verify staging and production lanes against the renamed stage environments
- remove compatibility aliases after rollout soak

### Rollback notes if any

- temporary legacy alias support remains in the loader for:
  - `dev`
  - `prod`
  - `staging-hq`
  - `kevins-desktop-hq-dev`
- if rollout stalls, existing bootstraps can continue to resolve through those
  aliases until the final cleanup phase
