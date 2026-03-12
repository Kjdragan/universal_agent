# Local Runtime Modes

Last updated: March 12, 2026

## Purpose

This document explains the two different local runtime lanes on Kevin's desktop.

They are intentionally different and should not be collapsed into one checkout.

## The Two Local Lanes

### 1. HQ Dev Lane

Use this for normal application development.

- checkout: `/home/kjdragan/lrepos/universal_agent`
- Infisical environment: `kevins-desktop-hq-dev`
- factory role: `HEADQUARTERS`
- deployment profile: `local_workstation`
- expected local ports:
  - web UI: `3000`
  - gateway: `8002`
  - API: `8001`

This is the lane that should serve:

- `/dashboard/todolist`
- `/dashboard/csi`
- `/dashboard/telegram`
- `/dashboard/corporation`

### 2. Desktop Worker Lane

Use this only when you want the desktop to participate in the factory fleet as a worker.

- checkout: `~/universal_agent_factory`
- Infisical environment: `kevins-desktop`
- factory role: `LOCAL_WORKER`
- deployment profile: `local_workstation`
- systemd user service: `universal-agent-local-factory.service`

This lane is not the full headquarters dashboard runtime.

## Important Rule

Do not point the main repo checkout at `kevins-desktop`.

If you do, localhost behaves like `LOCAL_WORKER` and the gateway correctly blocks most HQ dashboard routes with `403`.

## Bootstrap Commands

### HQ Dev

```bash
bash scripts/bootstrap_local_hq_dev.sh
```

If the Infisical workspace cannot create another environment yet, use the current fallback:

```bash
TARGET_ENV=dev bash scripts/bootstrap_local_hq_dev.sh
```

That keeps the repo checkout in HQ mode while the workspace remains under the current environment quota.

### Desktop Worker

```bash
bash scripts/deploy_local_factory.sh \
  --infisical-client-id ... \
  --infisical-client-secret ... \
  --infisical-project-id ... \
  --infisical-environment kevins-desktop
```

## Corporation Page Controls

When HQ dev is running on the same machine as the desktop worker, the Corporation page shows two different controls:

1. `Pause Intake`
   - logical delegation pause only
   - worker keeps running and heartbeating
2. `Stop Local Factory`
   - stops `universal-agent-local-factory.service`
   - use this when HQ development needs the local resources or shared coding/API budget

## Worker Port Isolation

HQ dev keeps the standard local gateway port:

- `8002`

Worker-only local gateway and tunnel helpers use:

- `8012`

This prevents worker-side helpers from colliding with the HQ dev lane.
