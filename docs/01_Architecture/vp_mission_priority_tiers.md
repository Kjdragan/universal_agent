# VP Mission Priority Tiers

**Last updated:** 2026-05-27
**Owner:** platform / VP infrastructure
**Status:** active (PR shipped 2026-05-27)

## Problem this design solves

Before this design, `vp_missions.priority` was a single numeric column
with a default of `100`, claimed by `ORDER BY priority ASC, created_at
ASC`. That created two bad properties:

1. **Lower number = more urgent**, opposite to many intuitions. The
   default of `100` therefore meant *lowest urgency*. Any caller who
   forgot to pass a priority got the worst possible placement.
2. **Operator-facing daily deliverables shared a numeric scale with
   automated proactive pipeline output.** Insight briefs, convergence
   briefs, and research reports were explicitly assigned
   `priority=3`–`5`. Briefings, the YouTube digest, curation, and
   proactive wiki accepted the default `priority=100`. Result: every
   time the proactive pipeline produced new insight briefs, they
   jumped the queue ahead of operator-facing daily work.

On 2026-05-27, this design flaw caused the morning briefing
(`mission_type=briefing`) to sit `queued` for 4+ hours behind ~110
priority-3/5 missions. The operator did not get their morning brief.

## The redesign

A semantic **tier** column lives above the numeric `priority` column.
Within a tier, numeric `priority` is the fine-grained tiebreaker.

```
ORDER BY
  CASE priority_tier
    WHEN 'operator_daily'  THEN 0
    WHEN 'operator_signal' THEN 1
    WHEN 'maintenance'     THEN 2
    ELSE                        3   -- 'background'
  END ASC,
  priority ASC,
  created_at ASC
```

### The four tiers

| Tier               | Meaning                                                                                                                                                                                                                                       | SLA expectation                                              |
|--------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| `operator_daily`   | Kevin reads it with morning coffee. Briefings, evening recap, YouTube daily digest.                                                                                                                                                           | Claimed within **2h** of dispatch. Beyond that he notices.    |
| `operator_signal`  | Atlas-generated proactive intelligence the operator wants but doesn't block on. Insight briefs, convergence briefs, research and research-report-email missions.                                                                              | Within reasonable hours; no hard cutoff.                      |
| `maintenance`      | System housekeeping. Curation, proactive wiki, doc-maintenance.                                                                                                                                                                                | Whenever the operator-facing tiers are caught up.             |
| `background`       | Opportunistic. The DEFAULT — applied to anything that doesn't have an explicit mapping. Runs only when the queue is otherwise clear.                                                                                                            | None.                                                         |

### Why this works

- **Self-documenting**: `priority_tier='operator_daily'` says exactly
  what it means; you don't need a comment to explain that "1 is the
  urgent one."
- **Safe default**: the column default is `'background'`, NOT
  `'operator_daily'`. Forgotten work runs LAST — it cannot starve
  briefings.
- **Single source of truth for `mission_type → tier`**: defined in
  [`src/universal_agent/vp/mission_priority.py`](../../src/universal_agent/vp/mission_priority.py).
  When you add a new mission type, add a row to `MISSION_TYPE_TIER`.
  Forgetting to do so leaves it in `background`, which delays but
  doesn't starve.
- **Numeric `priority` still works** within a tier as a fine-grained
  ordering — e.g. for two `operator_daily` missions, the one with the
  lower numeric `priority` wins.
- **`created_at` is the final tiebreaker** so the oldest queued item
  wins on a complete tie. Prevents starvation when many same-tier,
  same-priority items land at once.

## How to add a new mission type

1. Edit
   [`src/universal_agent/vp/mission_priority.py`](../../src/universal_agent/vp/mission_priority.py)
   and add an entry to `MISSION_TYPE_TIER` mapping your `mission_type`
   string to the correct tier.
2. That's it. The `dispatch_vp_mission` tool, the queue layer, and the
   claim ordering all read from this constants module — no other
   changes needed.

**If you forget step 1**: your mission type lands in the `background`
tier, runs when the queue is clear. Safe but delayed. No starvation
of higher tiers.

## Operator-facing freshness invariant

[`services/invariants/operator_daily_mission_freshness.py`](../../src/universal_agent/services/invariants/operator_daily_mission_freshness.py)
runs on every Simone heartbeat and emits a **critical** finding if any
`priority_tier='operator_daily'` mission has been queued >2h without
being claimed. Tunable via `UA_OPERATOR_DAILY_MISSION_SLA_HOURS` env
var. This closes the monitoring gap from the 2026-05-27 incident.

## Backlog tracking (informational)

[`services/vp_mission_backlog.py`](../../src/universal_agent/services/vp_mission_backlog.py)
samples the queue every heartbeat tick into
`vp_mission_backlog_history`, then computes trends (increasing /
decreasing / stable) over the last 30-minute and 6-hour windows.

The snapshot is surfaced in:
- `proactive_health` payload (`vp_mission_backlog` field)
- Simone's heartbeat context (informational — she decides whether to
  surface a notification)

The snapshot is **not** alerting on its own. The SLA invariant above
handles the alert side; the snapshot is just telemetry so Simone can
notice "the backlog has been growing for 6 hours" before it's a
crisis.

## Migration & backfill

- `durable/migrations.py` adds the `priority_tier` column with
  `TEXT NOT NULL DEFAULT 'background'`.
- The same migration runs a one-shot backfill: any row still at
  `priority_tier='background'` whose `mission_type` is in
  `MISSION_TYPE_TIER` gets updated to its mapped tier. Idempotent;
  manual operator overrides survive subsequent calls.
- `vp_mission_backlog_history` is a new table — no migration needed.

## Cleanup of `vp_sessions.last_error` stickiness

Pre-PR, `vp_sessions.last_error` was never cleared on success — a
4-day-old transient 401 stayed stamped on the row and misled
triage. Post-PR, `claim_next_vp_mission` clears it as part of a
successful claim. Any successful claim implies the VP is functional
now; preserving the historical error makes the table lie.

## Flush runbook

See [`docs/operations/vp_backlog_flush_runbook.md`](../operations/vp_backlog_flush_runbook.md)
for the one-shot `flush_vp_mission_backlog.py` script used to clear
the backlog post-deploy.

## Future considerations

- ZAI concurrency caps interact with VP claim cadence. If queue depth
  in `operator_signal` keeps growing without bound, the proactive
  pipeline is producing work faster than Atlas can consume it. The
  fix lives upstream (rate-limit the producers, not the consumers).
- We may want a per-tier max-age SLA, not just `operator_daily`.
- The 2h SLA threshold for `operator_daily` is conservative. If we
  reliably hit 30-minute end-to-end latency, we should tighten it.
