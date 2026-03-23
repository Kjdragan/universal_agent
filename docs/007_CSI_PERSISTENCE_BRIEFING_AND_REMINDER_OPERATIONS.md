# 007 - CSI Persistence, Briefing, and Reminder Operations

## Deployment Note

This document describes current CSI runtime behavior, but references to `deploy_vps.sh` below are historical notes about legacy script behavior.

- The current deployment contract for this repository is GitHub Actions.
- Push or merge to `develop` for staging and to `main` for production.
- Do not use `deploy_vps.sh` as the default deploy mechanism.

## Purpose

This document defines the production-safe CSI runtime pattern after the quality-first trend product upgrades:

1. Runtime CSI database has a single VPS source of truth.
2. Deploy no longer overwrites runtime DB state.
3. CSI now produces cross-source narrative briefings.
4. Briefing review reminders are emitted daily at 7:30 AM and 5:00 PM (America/Chicago).
5. Operators have a deterministic Tailscale preflight path.

## Runtime Data Contracts

### Canonical CSI DB path

- `CSI_DB_PATH=/var/lib/universal-agent/csi/csi.db`
- This path is now the canonical runtime location for CSI systemd units/scripts.
- Local repo `CSI_Ingester/development/var/` is not production runtime state.

### Deploy overwrite protection

`deploy_vps.sh` now excludes:

- `CSI_Ingester/development/var/`

This prevents local dev DB/files from overwriting VPS runtime history.

### One-time DB migration behavior

During deploy, if old DB exists and new DB does not:

- from: `/opt/universal_agent/CSI_Ingester/development/var/csi.db`
- to: `/var/lib/universal-agent/csi/csi.db`

The migration is applied automatically once.

## DB Backup and Restore

### Backup job

- Script: `CSI_Ingester/development/scripts/csi_db_backup_rotate.sh`
- Service: `csi-db-backup.service`
- Timer: `csi-db-backup.timer`
- Schedule: daily at `03:40` (server local time)
- Output dir: `/var/lib/universal-agent/csi/backups`
- Retention: `CSI_BACKUP_KEEP_DAYS` (default `14`)

### Backup command (manual)

```bash
sudo systemctl start csi-db-backup.service
journalctl -u csi-db-backup.service -n 100 --no-pager
```

### Restore command (manual)

```bash
sudo systemctl stop csi-ingester
LATEST=$(ls -1t /var/lib/universal-agent/csi/backups/csi-db-*.sqlite.gz | head -n1)
gunzip -c "$LATEST" | sudo tee /var/lib/universal-agent/csi/csi.db >/dev/null
sudo chown ua:ua /var/lib/universal-agent/csi/csi.db
sudo chmod 640 /var/lib/universal-agent/csi/csi.db
sudo systemctl start csi-ingester
```

## New CSI Analysis and Narrative Jobs

### Event-level enrichment parity

- Reddit enrichment:
  - Script: `csi_reddit_semantic_enrich.py`
  - Service/Timer: `csi-reddit-semantic-enrich.service/.timer`
  - Schedule: every 10 minutes
  - Table: `reddit_event_analysis`

- Threads enrichment:
  - Script: `csi_threads_semantic_enrich.py`
  - Service/Timer: `csi-threads-semantic-enrich.service/.timer`
  - Schedule: every 15 minutes
  - Table: `threads_event_analysis`

### Threads narrative reporting

- Script: `csi_threads_trend_report.py`
- Service/Timer: `csi-threads-trend-report.service/.timer`
- Schedule: hourly
- Emits event type: `threads_trend_report`
- Artifact path root: `/opt/universal_agent/artifacts/csi/threads_trend_reports/`

### Global cumulative briefing

- Script: `csi_global_trend_brief.py`
- Service/Timer: `csi-global-trend-brief.service/.timer`
- Schedule: every 2 hours
- Stores in table: `global_trend_briefs`
- Emits event type: `global_trend_brief_ready`
- Artifact path root: `/opt/universal_agent/artifacts/csi/global_trend_briefs/`

### Scheduled review reminders (notifications + personal Todo)

- Script: `csi_global_brief_reminder.py`
- Service/Timer: `csi-global-brief-reminder.service/.timer`
- Timer cadence: every 15 minutes (script gates to slot)
- Target review slots (America/Chicago):
  - `07:30`
  - `17:00`
- Emits event type: `csi_global_brief_review_due`
- Creates/upserts personal Todo tasks in:
  - Project: `UA: Immediate Queue`
  - Section: `Scheduled`
  - Labels: `personal-reminder`, `sleep-handoff`, `no-auto-exec`
  - Explicitly excludes `agent-ready`

### Threads rollout verification (post-phase-1 hardening)

- Script: `csi_threads_rollout_verify.py`
- Service/Timer: `csi-threads-rollout-verify.service/.timer`
- Schedule: daily `03:35 UTC`
- Verifies:
  - strict all-source Threads probe
  - DB event evidence in lookback window
  - semantic analysis row presence
  - Threads trend report presence
  - latest global brief Threads contribution

## Todoist Personal Upsert Contract

The Todo service now supports personal-only task creation/upsert:

- Method: `TodoService.create_personal_task(...)`
- Optional `upsert_key` marker stored in task description as:
  - `ua_upsert_key:<key>`

This allows deterministic recurring reminders without duplicate task explosion.

## CSI Dashboard API Updates

### Reports feed

`/api/v1/dashboard/csi/reports` now includes:

- `threads_trend_report`
- `global_trend_brief` (from `global_trend_briefs` table)
- Existing trend/insight/product/opportunity classes

### Briefings endpoint

New endpoint:

- `/api/v1/dashboard/csi/briefings`

This returns narrative classes suitable for briefing-first UX.

## Tailscale Reliability Runbook

### One-command preflight

```bash
scripts/tailscale_vps_preflight.sh
```

Or target explicit host:

```bash
scripts/tailscale_vps_preflight.sh root@uaonvps
```

### What preflight checks

1. `tailscale status`
2. `tailscale ping <host>`
3. SSH handshake test

If Tailscale interactive approval is required, the script explicitly flags it.

## Systemd Installation

Install/enable all CSI extras (including new jobs):

```bash
sudo /opt/universal_agent/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh
```

Validate timers:

```bash
systemctl status csi-reddit-semantic-enrich.timer
systemctl status csi-threads-semantic-enrich.timer
systemctl status csi-threads-trend-report.timer
systemctl status csi-global-trend-brief.timer
systemctl status csi-global-brief-reminder.timer
systemctl status csi-db-backup.timer
```
