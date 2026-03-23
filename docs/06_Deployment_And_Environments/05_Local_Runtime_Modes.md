# Local Runtime Modes

Last updated: March 19, 2026

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

## Desktop Transcript Worker (Always-On Requirement)

> [!IMPORTANT]
> The desktop **must be running** for YouTube transcript fetching to work via the primary path.
> Without the desktop, transcripts fall back to the VPS rotating proxy (more expensive, rate-limited).

The desktop transcript worker is a **standalone program** that runs independently
of both the HQ Dev Lane and the Desktop Worker Lane. It does not require any
gateway, factory, or Infisical bootstrap — it only needs:

1. SSH access to the VPS (`root@uaonvps`)
2. `youtube-transcript-api` installed (via `uv`)
3. Network access to YouTube (residential IP)

### How It Works

- checkout: `/home/kjdragan/lrepos/universal_agent`
- script: `src/universal_agent/desktop_transcript_worker.py`
- run mode: standalone CLI or cron/systemd timer
- NOT a UA cron job, NOT part of the gateway process

### Running It

```bash
# One-shot batch (fetch from VPS, process, write back)
DTW_BATCH_SIZE=25 uv run python src/universal_agent/desktop_transcript_worker.py --batch

# Dry run (show what would be processed)
uv run python src/universal_agent/desktop_transcript_worker.py --batch --dry-run

# Test specific videos (no VPS interaction)
uv run python src/universal_agent/desktop_transcript_worker.py --test daPwd4DnEfA avXA9Jgi-WE
```

### Scheduling

The worker can be scheduled via:

- **systemd timer**: `~/.config/systemd/user/desktop-transcript-worker.timer`
- **cron**: `0 * * * * cd /home/kjdragan/lrepos/universal_agent && uv run python src/universal_agent/desktop_transcript_worker.py --batch`
- **manual**: run on-demand as needed

### What Happens When the Desktop Is Off

When the desktop is not running:
- New videos accumulate in the CSI database with `transcript_status='failed'`
- The VPS proxy path in `youtube_ingest.py` continues to work as a fallback
- When the desktop comes back online, the next batch run picks up all pending videos

### Kill Switch

Set `DESKTOP_TRANSCRIPT_WORKER_ENABLED=false` to disable without uninstalling.
