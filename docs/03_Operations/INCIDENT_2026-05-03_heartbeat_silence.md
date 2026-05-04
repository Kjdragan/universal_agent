# Incident Note: Heartbeat & ToDo Dispatcher Silent Since 2026-05-01 23:45 UTC

> **Status:** Active operational issue, NOT YET RESOLVED.
> **Impact:** All proactive activity stopped (~26h+ as of 2026-05-03 ~02:00 UTC).
> **Detected by:** Mission Control Phase 1 sweeper (heartbeat tile flagged red).
> **Author:** Claude Code session investigating during Mission Control Phase 1B/1.1 deploys.
> **Note:** This document was created autonomously by the Claude Code session that surfaced the issue. Sandbox session does not have VPS log access so root-cause diagnosis is incomplete; the runbook below is what to do next.

## What we observe

Production probes after the Phase 1B deploy show:

| Signal | Value |
|---|---|
| Heartbeat last tick (`/api/v1/dashboard/todolist/overview` → `heartbeat.last_tick_at`) | **`None`** |
| ToDo dispatcher last wake (`dispatcher.last_wake_at`) | **`None`** |
| Heartbeat enabled flag (`heartbeat.enabled`) | `True` |
| Last `source_domain='heartbeat'` event in `activity_events` | **2026-05-01T23:45:14 UTC** |
| Cron pipelines | Running, but `csi_convergence_sync` failed today at 06:35 + 12:05 UTC |
| Production env `UA_DISABLE_HEARTBEAT` | `0` (not blocking) |
| Production env `UA_ENABLE_HEARTBEAT` | `1` (explicitly on) |

## Why it matters

Heartbeat is what drives the proactive cycle:
- Heartbeat-driven scheduled tasks (`proactive_codie`, idle dispatch loop, etc.)
- Tutorial-pipeline scheduling (the YouTube digest playlist work mentioned by Kevin)
- Auto-triage of stale tasks
- Daemon session keepalive

Without heartbeat ticks for 26+ hours, the system has been entirely passive — only cron-only work is firing, and even that has 2 failures today. This is why the dashboard's Proactive Task History tab has shown nothing new since ~01:31 AM, and why no YouTube playlist digests have been produced.

## Likely root causes (ranked by probability)

1. **Heartbeat service constructor succeeded but `start()` raised silently or hung**
   The gateway code path in `gateway_server.py:13909+` constructs `_heartbeat_service` then calls `_start_heartbeat_service()` via `_run_after_deployment_window`. If the `start()` coroutine raises after the deployment window closes, the exception lands in the `except` of `_spawn_background_task` and is logged but does not crash the gateway.

2. **A code change to heartbeat between 2026-04-30 and 2026-05-01 introduced a startup-time error.**
   Recent commits touching heartbeat:
   - `e439604c` — fix(viewer/dashboard): three operational papercuts surfaced by the heartbeat trace
   - `6a328ca7` — fix(viewer+dispatch): full-fidelity daemon trace + stuck-task auto-disposition + sessions-tab parity

3. **A daemon dependency (DaemonSessionManager, factory registry) raised during init and the heartbeat coroutine never reached `start()`.**

4. **The heartbeat IS ticking but its ticks no longer write `source_domain='heartbeat'` events** (the detection channel changed). Less likely because `last_tick_at` is also `None` in the runtime info — the runtime memory state agrees the heartbeat hasn't ticked.

## Diagnostic commands to run on the VPS

SSH into the production VPS, then:

```bash
# 1. Check the gateway service status + recent logs
sudo systemctl status universal-agent --no-pager
sudo journalctl -u universal-agent -n 1000 --no-pager | grep -E -i "heartbeat|HeartbeatService|heart.*beat" | tail -100

# 2. Look for startup-time exceptions during the most recent deploy
sudo journalctl -u universal-agent --since "2 hours ago" --no-pager | grep -E -i "Traceback|Failed.*heartbeat|heartbeat.*fail|exception.*heartbeat"

# 3. Confirm whether the heartbeat coroutine is registered as a running task
# (this is the smoking gun if it never started; will be 0 lines if dead)
sudo journalctl -u universal-agent --since "2 hours ago" --no-pager | grep -E "💓|Heartbeat session seed complete|Heartbeat System ENABLED"

# 4. Check if the deployment window sentinel is permanently active (would block startup tasks)
sudo journalctl -u universal-agent --since "2 hours ago" --no-pager | grep -E "deployment window|_run_after_deployment_window"

# 5. If heartbeat startup is actually firing but ticks aren't producing events,
# look at the heartbeat service tick loop directly
sudo journalctl -u universal-agent --since "1 hour ago" --no-pager | grep -E "heartbeat.tick|heartbeat_tick|_emit_heartbeat_event"
```

## Quick-recovery options if the runbook above is inconclusive

These are higher-blast-radius — confirm with operator before applying:

1. **Restart the gateway service** (lifecycle restart often re-resolves transient init failures):
   ```bash
   sudo systemctl restart universal-agent
   sudo journalctl -u universal-agent -n 200 --no-pager -f
   ```
   Look for `💓 Heartbeat session seed complete (N sessions)` after the restart. If you see it, heartbeat resumed and proactive activity should restart within ~minutes.

2. **If restart doesn't help**, temporarily disable any recent guard-style code path in heartbeat startup. The two recent commits to investigate are `e439604c` and `6a328ca7`.

## Why the Mission Control Phase 1 work surfaced this

This is exactly what the tile system was built to expose. Without the heartbeat tile, this 26h outage would have continued unnoticed because:
- The Chief-of-Staff readout was generated 11 hours ago — also stale
- The Proactive Task History tab shows "no new activity" but doesn't say WHY
- The Events tab is full of repetitive cron-failure noise that buries the heartbeat-silence signal

Once the heartbeat is restored AND the new Phase 2 tier-1 LLM-discovered cards are in production, this kind of issue will surface as a synthesized critical card with auto-diagnostic context, not just a colored tile.

## Status updates

- 2026-05-03 ~02:00 UTC: Issue detected during Mission Control Phase 1 smoke testing.
- _Awaiting operator log-grep + likely service restart._

---

## Resolution (2026-05-04)

**Status: RESOLVED.** The immediate symptom (heartbeat tile reading red) was
resolved when the operator restarted the gateway service. The architectural
hole that allowed the silence to go undetected for 26+ hours is now closed.

### Why this won't recur silently

Phase 4 of the proactive-robustness plan (commit `168f5288` —
`feat(observability): surface background-task and service-startup failures`)
closed the architectural hole that allowed the original silence to vanish into
asyncio without operator-visible signal:

- `_spawn_background_task` (gateway_server.py:8472) now installs an
  `add_done_callback` on every spawned task. If the task raises a non-cancel
  exception, a `kind=background_task_failed` notification fires immediately
  with the task name in metadata. The next time heartbeat (or any other
  background task) dies for any reason, the dashboard goes red within seconds
  AND the F3 dispatcher (commit `dd3f2fa8`) emails + Telegrams the alert
  out-of-band.
- `_run_after_deployment_window` (gateway_server.py:13808) now wraps the
  inner coroutine in try/except. A startup-time failure (the leading
  hypothesis for this incident) emits a `kind=service_startup_failed`
  notification with `metadata.component` naming the service that failed to
  start.

Both kinds are in `_HEALTH_ALERT_NOTIFICATION_KINDS`, so a flapping startup
collapses to one live row instead of stacking.

### Diagnostic tooling for ad-hoc liveness checks

`scripts/check_heartbeat_liveness.py` (new, commit landing alongside this
note) reads the dashboard overview's heartbeat block and exits non-zero
when:
- `latest_last_run_epoch is None` (the original silence shape — exit 2).
- Last tick is older than 2x the configured interval (exit 3).
- Dashboard API itself returned an error (exit 1).

Run on the VPS at any time: `python scripts/check_heartbeat_liveness.py`.

For end-to-end alerting verification, `scripts/probe_notification_dispatch.py`
(also new, commit `63785cdb`) posts a synthetic high-severity notification
and confirms the email + Telegram dispatch path delivers within 90s.

### Closed by

- Phase 4 commit `168f5288` (background-task / service-startup notifications).
- F3 commit `dd3f2fa8` (async email/Telegram dispatcher).
- G5 commits (this note + the new diagnostic script).
- G1 commit `63785cdb` (synthetic-notification probe).
