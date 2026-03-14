# 78 Daily Autonomous Briefing Reliability And Input Diagnostics (2026-02-26)

## Executive Summary
The daily autonomous briefing can appear low-value when upstream autonomous inputs are empty, disabled, or lost after runtime resets. We implemented briefing hardening so the report now explains input health explicitly instead of silently showing empty sections.

## Incident Observations
- Briefing output showed little to no meaningful activity.
- Persisted cron history showed only the daily briefing job run in the window.
- Prior autonomous state was likely reset/removed during cleanup.
- CSI/Todoist upstream paths had known instability around this period.

## Root Cause
1. The briefing generator depended primarily on in-memory notification state.
2. In-memory notification state is volatile across restarts.
3. When volatile state is empty, the report looked like a no-op without diagnosis.

## Implemented Hardening
1. Added cron-history backfill:
   - When autonomous notification rows are empty, pull autonomous run outcomes from persisted cron run history.
2. Added source diagnostics to briefing outputs:
   - notification counts in window
   - persisted cron run counts
   - autonomous classification counts
   - daily briefing self-run exclusion count
   - backfill-applied indicator
3. Added explicit data-quality warnings:
   - no autonomous events in window
   - only daily briefing self-run observed
   - missing metadata for classifying persisted runs
4. Added runtime dependency checks surfaced in report diagnostics:
   - `UA_SIGNALS_INGEST_ENABLED`
   - presence of Todoist credentials (`TODOIST_API_TOKEN` or `TODOIST_API_KEY`)

## Why This Matters
The briefing is a critical control-plane document. Empty output should be diagnosable, not ambiguous. The new diagnostics make it clear whether:
- there was truly no autonomous work, or
- input processes were broken/disabled/reset.

## Validation
- Added targeted regression tests in `tests/gateway/test_cron_notifications.py` for:
  - cron backfill behavior
  - warning behavior when only briefing self-run exists
- Test result: all tests in the module passed.

## Operational Recommendations
1. Monitor `briefing.json.warnings` and `briefing.json.source_diagnostics` in dashboard automation.
2. Alert on recurring pattern: `completed=0`, `failed=0`, `heartbeat=0` with non-empty warnings.
3. Preserve/backup `AGENT_RUN_WORKSPACES/cron_runs.jsonl` and `cron_jobs.json`.
4. Ensure service runtime env parity for:
   - CSI ingest enablement and shared secret
   - Todoist credentials
5. Keep daily briefing self-run excluded from autonomous “work completed” counts.
