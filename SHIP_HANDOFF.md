# SHIP_HANDOFF

**Summary of changes made:**
Mission Control: CSI tile cadence fix + cron CancelledError handler

**What ships:**
- CSI Ingester tile stops sitting in red ~22h/day. Thresholds retuned from hourly assumption (1h/6h/24h) to actual twice-daily polling cadence (12h/25h/48h). Healthy system now reads green between scheduled polls instead of alarming.
- Cron run cards stop painting red "Cron Run Failed" on every deploy. `asyncio.CancelledError` (BaseException subclass in Py3.8+) was bypassing the generic `except Exception` in `cron_service._run_job`, leaving in-flight runs unfinalized and triggering phantom failures from the recovery sweep on next startup. Now caught explicitly → status='cancelled' → info-severity cron_run_cancelled notification → hidden by default in /dashboard/events.
- No env flips needed. Both fixes activate on deploy. No new feature flags.

**List of commits:**
- 257808c1 — fix(mission-control): tune CSI tile cadence + handle cron CancelledError
- b969600a — docs(mission-control): document CSI cadence + cron-cancellation handling
- e0d6f675 — chore(memory): capture proactive task rollover snapshots
