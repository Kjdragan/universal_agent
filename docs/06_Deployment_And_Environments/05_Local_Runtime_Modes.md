# Local Runtime Modes

Last updated: March 12, 2026

## Purpose

This document explains the two supported local runtime lanes on Kevin's
desktop under the stage-based Infisical model.

## The Two Local Lanes

### 1. HQ Dev Lane

Use this for normal localhost application development.

- checkout: `/home/kjdragan/lrepos/universal_agent`
- Infisical environment: `development`
- runtime stage: `development`
- factory role: `HEADQUARTERS`
- deployment profile: `local_workstation`
- machine slug: `kevins-desktop`
- expected local ports:
  - web UI: `3000`
  - gateway: `8002`
  - API: `8001`

This is the lane that should serve:

- `/dashboard/todolist`
- `/dashboard/csi`
- `/dashboard/telegram`
- `/dashboard/corporation`

Bootstrap with:

```bash
bash scripts/bootstrap_local_hq_dev.sh
```

### 2. Desktop Worker Lane

Use this only when you want the desktop to participate in staging or
production as a local worker.

- checkout: `~/universal_agent_factory`
- Infisical environment: `staging` or `production`
- runtime stage: `staging` or `production`
- factory role: `LOCAL_WORKER`
- deployment profile: `local_workstation`
- machine slug: `kevins-desktop`
- systemd user service: `universal-agent-local-factory.service`

Bootstrap with:

```bash
bash scripts/bootstrap_local_worker_stage.sh --stage staging
```

or:

```bash
bash scripts/bootstrap_local_worker_stage.sh --stage production
```

## Important Rule

Do not point the main repo checkout at a worker bootstrap.

If localhost starts returning role-based `403` responses on HQ dashboard
pages, the repo checkout is almost certainly no longer bootstrapped as:

- `INFISICAL_ENVIRONMENT=development`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=local_workstation`

## Corporation Page Controls

When HQ dev is running on the same machine as the desktop worker, the
Corporation page shows two different controls:

1. `Pause Intake`
   - logical delegation pause only
   - worker keeps running and heartbeating
2. `Stop Local Factory`
   - stops `universal-agent-local-factory.service`
   - use this when HQ development needs the local resources or shared budget

## Worker Port Isolation

HQ dev keeps the standard local gateway port:

- `8002`

Worker-only helpers can use:

- `8012`

This prevents worker-side helpers from colliding with the HQ dev lane.
