# Cron Job Registration — Source of Truth & First-Boot Fallback

> **Last updated:** 2026-05-04 — established after the proactive-robustness
> work (commits `825e608e` through `15ccbc4e`) made all proactive cron jobs
> idempotently re-registerable at gateway boot.

## Source of truth

The canonical source of truth for proactive cron jobs is the set of
`_ensure_*_cron_job()` helpers in `src/universal_agent/gateway_server.py`,
called from the lifespan startup block at `gateway_server.py:14215-14227`:

```
_ensure_autonomous_daily_briefing_job()
_ensure_codie_proactive_cleanup_cron_job()
_ensure_csi_convergence_cron_job()
_ensure_claude_code_intel_cron_job()
_ensure_paper_to_podcast_cron_job()
_ensure_youtube_daily_digest_cron_job()
_ensure_nightly_wiki_cron_job()
_ensure_morning_briefing_cron_job()
_ensure_proactive_report_morning_cron_job()
_ensure_proactive_report_midday_cron_job()
_ensure_proactive_report_afternoon_cron_job()
_ensure_proactive_artifact_digest_cron_job()
```

Each helper is **idempotent**: it looks up the existing job by
`metadata.system_job` via `_find_cron_job_by_system_job` and either calls
`update_job` (if found) or `add_job` (if missing). Running the helpers on
every gateway start makes drift self-healing — a fresh deploy or a wiped
state file will re-register every job in its canonical shape.

## Role of `workspaces/cron_jobs.json`

`workspaces/cron_jobs.json` is a **first-boot fallback**, not the source of
truth. The cron service loads it during `CronService.__init__`
(`cron_service.py:515`) which runs **before** the lifespan boot block
calls the `_ensure_*` helpers (`gateway_server.py:14227`). This means:

- On a fresh checkout where the runtime DB has no jobs yet, the seed file
  populates the initial set so the scheduler has something to schedule
  before the helpers fire.
- On a normal restart, the helpers immediately reconcile any drift between
  the seed and the canonical config (cron expression, command, timeout,
  metadata, `catch_up_on_restart` flag).

Because all 7 entries in the seed file now have matching `_ensure_*`
helpers with identical cron expressions, the seed is essentially redundant
in steady state. We keep it as a deliberate fallback rather than delete
it, because deletion would create a small window during fresh-state
startup where the gateway has no jobs scheduled.

If the seed file ever drifts from the helpers, the helpers win — they run
last and their `update_job` call overwrites the seed's values. Future
work could add a boot-time reconciliation log that warns about drift, but
it's not necessary today (drift is self-healing).

## Adding a new proactive cron job

Mirror `_ensure_paper_to_podcast_cron_job` (`gateway_server.py:17883`).
Steps:

1. Add a `<NAME>_JOB_KEY` constant, default cron expression, default
   timezone constants alongside the others (`gateway_server.py:432-455`).
2. Add an `<env>_enabled()` helper that returns False unless the env flag
   is on.
3. Add `_ensure_<name>_cron_job()` that calls
   `_register_system_cron_job(...)` (`gateway_server.py:~17750`) — pass
   `system_job`, `default_cron`, `default_timezone`, `command`,
   `description`, `timeout_seconds`, `enabled`, optionally
   `cron_env_var` / `timezone_env_var` / `required_secrets`.
4. Add the helper call to the lifespan block at `gateway_server.py:14215`.
5. (Optional but recommended) declare any truly job-specific env vars in
   `metadata.required_secrets`. The cron service pre-flight check
   (`cron_service._find_missing_required_secrets`) will fail the run with
   a structured `cron_run_failed` notification naming the missing keys
   before the script even starts.

## Disabling a job in production

Every `_ensure_*` helper is gated by an `<env>_enabled()` flag, so any
job can be disabled at deploy time by adding the env var to `.env`
(or Infisical) on the production VPS:

| Job | Disable env |
|---|---|
| `autonomous_daily_briefing` | `UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED=0` (default off as of G2) |
| `codie_proactive_cleanup` | `UA_CODIE_PROACTIVE_CLEANUP_ENABLED=0` |
| `csi_convergence_sync` | `UA_CSI_CONVERGENCE_CRON_ENABLED=0` |
| `claude_code_intel` | `UA_CLAUDE_CODE_INTEL_CRON_ENABLED=0` |
| `paper_to_podcast_daily` | `UA_PAPER_TO_PODCAST_ENABLED=0` |
| `youtube_daily_digest` | `UA_YOUTUBE_DAILY_DIGEST_ENABLED=0` |
| `nightly_wiki` | `UA_NIGHTLY_WIKI_ENABLED=0` |
| `morning_briefing` | `UA_MORNING_BRIEFING_ENABLED=0` |
| `proactive_report_*` | `UA_PROACTIVE_REPORTS_ENABLED=0` (covers all three time slots) |
| `proactive_artifact_digest` | `UA_PROACTIVE_ARTIFACT_DIGEST_ENABLED=0` |

Disabling via env doesn't delete the job from the runtime DB; it just
makes the helper skip the upsert on next boot, leaving the existing row
in whatever state it was in. To fully delete, also run a `DELETE FROM
cron_jobs WHERE job_id = ...` against the runtime SQLite, or hit
`DELETE /api/v1/ops/cron/jobs/{job_id}`.

## Verifying a job ran

After a scheduled tick:

1. **Recent runs:** `GET /api/v1/ops/cron/jobs/{job_id}/runs` returns the
   last N `CronRunRecord` entries from `cron_runs.jsonl`.
2. **Dashboard notifications:** any failure surfaces as a kind-upserted
   `cron_run_failed` (or `autonomous_run_failed`) notification visible
   on the dashboard. Successes show as `cron_run_success` /
   `autonomous_run_completed` (info severity).
3. **Out-of-band alerting:** error/warning notifications also drain to
   email + Telegram via the `NotificationDispatcher` (default 30s polling
   interval, 5min per-kind cooldown).
4. **Synthetic verification:** run
   `python scripts/probe_notification_dispatch.py --wait 90 --cleanup`
   to confirm the dispatcher path is healthy end-to-end.
5. **Heartbeat liveness:** run
   `python scripts/check_heartbeat_liveness.py` to confirm the gateway's
   heartbeat is currently ticking. Stale heartbeat usually correlates
   with broader background-task issues that affect cron reliability.

## Related files

- `src/universal_agent/gateway_server.py` — `_ensure_*_cron_job` helpers
  and lifespan boot block.
- `src/universal_agent/cron_service.py` — `CronService`, `CronJob`,
  `CronStore`, `_find_missing_required_secrets`.
- `workspaces/cron_jobs.json` — first-boot fallback seed.
- `scripts/probe_notification_dispatch.py` — synthetic-notification probe.
- `scripts/check_heartbeat_liveness.py` — heartbeat liveness check.
- `docs/03_Operations/INCIDENT_2026-05-03_heartbeat_silence.md` — original
  incident that motivated this robustness work.
