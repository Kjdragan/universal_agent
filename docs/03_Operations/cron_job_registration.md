# Cron Job Registration — Source of Truth & First-Boot Fallback

> **Last updated:** 2026-05-25 — established after the proactive-robustness
> work (commits `825e608e` through `15ccbc4e`) made all proactive cron jobs
> idempotently re-registerable at gateway boot. Updated to reflect the
> full 22-helper startup block (was 13 in the previous revision).

## Source of truth

The canonical source of truth for proactive cron jobs is the set of
`_ensure_*_cron_job()` helpers in `src/universal_agent/gateway_server.py`,
called from the lifespan startup block at `gateway_server.py:14743-14764`:

```
_ensure_codie_proactive_cleanup_cron_job()
_ensure_csi_convergence_cron_job()
_ensure_claude_code_intel_cron_job()
_ensure_csi_demo_triage_rank_cron_job()
_ensure_intel_auto_promoter_cron_job()
_ensure_paper_to_podcast_cron_job()
_ensure_youtube_daily_digest_cron_job()
_ensure_youtube_gold_poller_cron_job()
_ensure_nightly_wiki_cron_job()
_ensure_morning_briefing_cron_job()
_ensure_proactive_report_morning_cron_job()
_ensure_proactive_report_midday_cron_job()
_ensure_proactive_report_afternoon_cron_job()
_ensure_proactive_artifact_digest_cron_job()
_ensure_cron_artifact_reminders_sweep_cron_job()
_ensure_vp_coder_workspace_pruning_cron_job()
_ensure_vp_mission_pr_reconciler_cron_job()
_ensure_architecture_canvas_drift_cron_job()
_ensure_hackernews_snapshot_cron_job()
_ensure_atlas_direct_dispatch_cron_job()
_ensure_simone_chat_autocomplete_cron_job()
_ensure_vault_lint_contradictions_cron_job()
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
calls the `_ensure_*` helpers (`gateway_server.py:14764`). This means:

- On a fresh checkout where the runtime DB has no jobs yet, the seed file
  populates the initial set so the scheduler has something to schedule
  before the helpers fire.
- On a normal restart, the helpers immediately reconcile any drift between
  the seed and the canonical config (cron expression, command, timeout,
  metadata, `catch_up_on_restart` flag).

Because entries in the seed file now have matching `_ensure_*`
helpers with identical cron expressions, the seed is essentially redundant
in steady state. We keep it as a deliberate fallback rather than delete
it, because deletion would create a small window during fresh-state
startup where the gateway has no jobs scheduled.

If the seed file ever drifts from the helpers, the helpers win — they run
last and their `update_job` call overwrites the seed's values. Future
work could add a boot-time reconciliation log that warns about drift, but
it's not necessary today (drift is self-healing).

## ⚠️ Z.AI peak-time scheduling (read before picking a `default_cron`)

The Z.AI proxy (`api.z.ai/api/anthropic`) is capacity-limited during
peak demand from its primary user base, which follows **Greater-China
business hours**. Because China is UTC+8 and our default timezone is
`America/Chicago` (UTC−5 in CDT), **US night maps to China afternoon /
evening peak**. Running heavy LLM-bound crons overnight US-time —
the intuitive "do batch work when nobody is around" — puts them
squarely into the worst capacity window.

**Rule of thumb when picking a new `default_cron`:**

| US Central (CDT) | China demand | Use for cron? |
|---|---|---|
| 00:00–10:00 (US night → US morning) | 🔴 Peak | **Avoid** for LLM-heavy jobs |
| 10:00–14:00 (US lunch) | ✅ Off-peak (China night) | **Prefer** for proactive heavy lanes |
| 14:00–17:00 (US afternoon) | ✅ Off-peak | **Prefer** for cleanup / low-priority |
| 18:00–22:00 (US evening) | 🟡 Ramp-up | OK for light jobs |

For the full audit of existing crons, the proposed reschedule, and the
operator-idle-detection follow-up plan, see
[`docs/operations/2026-05-08_zai_peak_time_scheduling.md`](../operations/2026-05-08_zai_peak_time_scheduling.md).

A "heavy LLM-bound" cron is anything that issues more than ~10 LLM
calls per fire (CSI lanes, briefing/report generators, vault backfills,
research pipelines). Light jobs (file pruning, single-LLM digests,
notification sweeps) are insensitive to the peak window and can fire
whenever else makes sense.

## Adding a new proactive cron job

Mirror `_ensure_paper_to_podcast_cron_job` (`gateway_server.py:18762`).
Steps:

1. Add a `<NAME>_JOB_KEY` constant, default cron expression, default
   timezone constants alongside the others (`gateway_server.py:432-455`).
2. Add an `<env>_enabled()` helper that returns False unless the env flag
   is on.
3. Add `_ensure_<name>_cron_job()` that calls
   `_register_system_cron_job(...)` (`gateway_server.py:~18650`) — pass
   `system_job`, `default_cron`, `default_timezone`, `command`,
   `description`, `timeout_seconds`, `enabled`, optionally
   `cron_env_var` / `timezone_env_var` / `required_secrets`.
4. Add the helper call to the lifespan block at `gateway_server.py:14743`.
5. (Optional but recommended) declare any truly job-specific env vars in
   `metadata.required_secrets`. The cron service pre-flight check
   (`cron_service._find_missing_required_secrets`) will fail the run with
   a structured `cron_run_failed` notification naming the missing keys
   before the script even starts.

## Schedule constraints

**Minimum interval — `every_seconds ≥ 60s` (PR #218, 2026-05-11).** The
cron API supports two schedule modes: cron expressions (`cron_expr`,
preferred for almost everything) and simple intervals (`every_seconds`).
After a real incident where two test crons with `every_seconds=2`
generated ~30 retry-storm warning emails over 10 hours,
`cron_service.add_job` and `update_job` now reject any `every_seconds`
value below `MIN_CRON_INTERVAL_SECONDS = 60`. If you need finer-grained
scheduling, the error message points you to `cron_expr`.

This guard does NOT apply to internal periodic loops (heartbeat,
factory-staleness sweep, `_vp_stale_reconcile_loop`) — those run as
async tasks in the gateway lifespan, not as cron rows.

## Lightweight cron path

**Added in PR #379 (2026-05-19).** The standard cron path runs through
`gateway.create_session()` before every cron tick, which synchronously
discovers Composio apps, builds a ~54 KB capability snapshot, loads SOUL,
and registers a session dossier — blocking the gateway asyncio event loop
for 3–14 seconds per tick. For high-frequency housekeeping crons that
only need a simple `!script` subprocess, this overhead is unnecessary
and causes visible latency (the dashboard's 4 s `/api/v1/version` health
check times out, surfacing the red "Gateway unreachable" banner).

The **lightweight path** bypasses `create_session` entirely. When a cron
job is registered with `lightweight=True` in its metadata, `_run_job`
runs the `!script` command directly via subprocess, reusing the existing
persist/finalize/cleanup logic but skipping session bootstrap.

**Registration:** pass `lightweight=True` to `_register_system_cron_job`.
The cron service validates at registration time that the command starts
with `!script` — misconfigured lightweight jobs raise `ValueError` at
gateway startup (fail-fast).

**Current lightweight jobs:**
- `simone_chat_auto_complete` (every 60 s) — runs a 5-line SQL UPDATE to
  finalize stale in-progress chat sessions. Does not need LLM access or
  session context.

**When to use lightweight:** pure housekeeping crons that run simple
database updates, file operations, or other non-LLM work at high
frequency (1–5 min intervals). Do NOT use for LLM-bound jobs that need
session context, capability injection, or agent identity.

## Disabling a job in production

Every `_ensure_*` helper is gated by an `<env>_enabled()` flag, so any
job can be disabled at deploy time by adding the env var to `.env`
(or Infisical) on the production VPS:

| Job | Disable env |
|---|---|
| `codie_proactive_cleanup` | `UA_CODIE_PROACTIVE_CLEANUP_ENABLED=0` |
| `csi_convergence_sync` | `UA_CSI_CONVERGENCE_CRON_ENABLED=0` |
| `claude_code_intel` | `UA_CLAUDE_CODE_INTEL_CRON_ENABLED=0` |
| `csi_demo_triage_rank` | `UA_CSI_DEMO_TRIAGE_RANK_CRON_ENABLED=0` |
| `intel_auto_promoter` | `UA_INTEL_AUTO_PROMOTE_CRON_ENABLED=0` |
| `paper_to_podcast_daily` | `UA_PAPER_TO_PODCAST_ENABLED=0` |
| `youtube_daily_digest` | `UA_YOUTUBE_DAILY_DIGEST_ENABLED=0` |
| `youtube_gold_poller` | `UA_YOUTUBE_GOLD_POLLER_ENABLED=0` |
| `nightly_wiki` | `UA_NIGHTLY_WIKI_ENABLED=0` |
| `morning_briefing` | `UA_MORNING_BRIEFING_ENABLED=0` |
| `proactive_report_*` | `UA_PROACTIVE_REPORTS_ENABLED=0` (covers all three time slots) |
| `proactive_artifact_digest` | `UA_PROACTIVE_ARTIFACT_DIGEST_ENABLED=0` |
| `cron_artifact_reminders_sweep` | `UA_CRON_ARTIFACT_REMINDERS_ENABLED=0` |
| `vp_coder_workspace_pruning` | `UA_VP_CODER_WORKSPACE_PRUNING_ENABLED=0` |
| `vp_mission_pr_reconciler` | `UA_VP_MISSION_PR_RECONCILER_ENABLED=0` |
| `architecture_canvas_drift` | `UA_ARCH_CANVAS_DRIFT_ENABLED=0` |
| `hackernews_snapshot` | `UA_HACKERNEWS_SNAPSHOT_ENABLED=0` |
| `atlas_direct_dispatch` (Hermes Phase C, default OFF) | `UA_ATLAS_DIRECT_DISPATCH_ENABLED=0` |
| `simone_chat_auto_complete` (lightweight, always on) | `UA_SIMONE_CHAT_AUTOCOMPLETE_ENABLED=0` |
| `vault_lint_contradictions` | `UA_VAULT_LINT_CONTRADICTIONS_ENABLED=0` |

> **`atlas_direct_dispatch` (Hermes Phase C, PR #221):** independent
> dispatcher that bypasses Simone's heartbeat throttle for tasks tagged
> `metadata.preferred_vp = "vp.general.primary"`. Runs every 60s when
> enabled. Default OFF; operator opts in via
> `UA_ATLAS_DIRECT_DISPATCH_ENABLED=1` after dry-run. See
> [`docs/reports/hermes-adaptation-phased-plan-2026-05-10.md`](../reports/hermes-adaptation-phased-plan-2026-05-10.md)
> § Phase C for the full design.

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
